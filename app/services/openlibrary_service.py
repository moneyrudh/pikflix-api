import asyncio
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from functools import cache
from typing import Any, List, Optional

import httpx

from app.config import OPENLIBRARY_BASE_URL, OPENLIBRARY_REQUESTS_PER_SECOND, OPENLIBRARY_USER_AGENT
from app.models import OpenLibraryDoc
from app.schemas import BookPick

logger = logging.getLogger(__name__)

# `editions` returns the best-matching edition of each work, whose title is in the language searched
SEARCH_FIELDS = ",".join([*OpenLibraryDoc.model_fields, "editions.title"])
SEARCH_LIMIT = 5

# How close a search result must be to Claude's pick to count as the same book. Titles collide
# constantly (dozens of books called "Dune" or "Atonement"), so title alone is never enough.
TITLE_SIMILARITY = 0.85
YEAR_TOLERANCE = 2  # catalogue years drift (republications) or are plain wrong (Pale Fire is listed as 1945)

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _fold(text: str) -> str:
    """Casefold and strip accents, so "Márquez" matches "Marquez"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_title(title: str) -> str:
    """Comparable form of a title: no subtitle, leading article, punctuation or accents."""
    t = _fold(title).replace("&", "and")
    t = re.split(r"[:(\[]", t, maxsplit=1)[0]   # "Dune: Deluxe Edition" -> "dune"
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"^(the|a|an)\s+", "", t.strip())
    return " ".join(t.split())


def _name_tokens(name: str) -> List[str]:
    return [tok for tok in re.sub(r"[^\w\s]", " ", _fold(name)).split() if tok not in _NAME_SUFFIXES]


def author_surname(name: str) -> str:
    tokens = _name_tokens(name)
    return tokens[-1] if tokens else ""


def lookup_key(title: str, author: str) -> str:
    """Cache key for a book: normalized title plus author surname."""
    return f"{normalize_title(title)}|{author_surname(author)}"


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def _title_similarity(pick: BookPick, doc: OpenLibraryDoc) -> float:
    """Best similarity between the pick's title and the work's (original or matched edition) titles."""
    return max(_similarity(pick.title, title) for title in doc.titles)


def display_title(pick: BookPick, doc: OpenLibraryDoc) -> str:
    """The catalogue's title when the work's own title matches; otherwise (a translated work matched
    through an edition) the pick's title, since edition records are often oddly cased ("WAITING FOR GODOT")."""
    if _similarity(pick.title, doc.title) >= TITLE_SIMILARITY:
        return doc.title
    return pick.title


def _same_person(pick_name: str, catalogue_name: str) -> bool:
    """Every part of the pick's name matches a part of the catalogue name, as a word or an initial.
    Order-free ("Liu Cixin" / "Cixin Liu"), initial-aware ("J.R.R." / "J. R. R."), and it tells
    Frank Herbert from Brian Herbert."""
    theirs = _name_tokens(catalogue_name)
    ours = _name_tokens(pick_name)
    if not ours or author_surname(pick_name) not in theirs:
        return False
    return all(any(a == b or (min(len(a), len(b)) == 1 and a[0] == b[0]) for b in theirs) for a in ours)


def is_match(pick: BookPick, doc: OpenLibraryDoc) -> bool:
    """Same title (fuzzy) and same author. Year isn't required: catalogue years are often wrong."""
    return (_title_similarity(pick, doc) >= TITLE_SIMILARITY
            and any(_same_person(pick.author, name) for name in doc.author_name))


def _year_agrees(pick: BookPick, doc: OpenLibraryDoc) -> bool:
    if pick.year is None or doc.first_publish_year is None:
        return True
    return abs(pick.year - doc.first_publish_year) <= YEAR_TOLERANCE


def best_match(pick: BookPick, docs: List[OpenLibraryDoc]) -> Optional[OpenLibraryDoc]:
    """The search result that is verifiably the book Claude meant, or None. Never guesses."""
    matches = [doc for doc in docs if is_match(pick, doc)]
    if not matches:
        return None
    # Prefer a year that agrees, then the closest title, then the most editions (Open Library often
    # has stray duplicate works; the real one has by far the most editions)
    best = max(matches, key=lambda doc: (_year_agrees(pick, doc), _title_similarity(pick, doc), doc.edition_count or 0))
    if not _year_agrees(pick, best):
        logger.info("Accepted '%s' despite year %s vs catalogue %s", pick.title, pick.year, best.first_publish_year)
    return best


def parse_description(raw: Any) -> Optional[str]:
    """A work's description is either a string or {"type": "/type/text", "value": "..."}."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


class OpenLibraryError(Exception):
    """Open Library didn't answer usefully: timeout, connection failure, or an error status."""


class RateLimiter:
    """Spaces calls at least 1/rate seconds apart across every request in this process."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1 / per_second
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:  # only booking a slot is serialized; the waiting isn't
            now = loop.time()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        await asyncio.sleep(slot - now)


class OpenLibraryService:
    def __init__(self) -> None:
        self.base_url = OPENLIBRARY_BASE_URL
        self.client = httpx.AsyncClient(timeout=15.0, headers={"User-Agent": OPENLIBRARY_USER_AGENT})
        self.limiter = RateLimiter(OPENLIBRARY_REQUESTS_PER_SECOND)

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: Optional[dict] = None) -> Any:
        await self.limiter.wait()
        try:
            response = await self.client.get(f"{self.base_url}{path}", params=params)
        except httpx.HTTPError as e:  # timeouts and connection errors; Open Library is sometimes slow
            raise OpenLibraryError(f"{path}: {e.__class__.__name__}") from e
        if response.status_code != 200:
            raise OpenLibraryError(f"{path}: HTTP {response.status_code}")
        return response.json()

    async def search(self, title: str, author: str) -> List[OpenLibraryDoc]:
        """Matching works, or [] if Open Library can't be reached (the pick is then dropped)."""
        # q=, not title=: title= only matches a work's main title, which for translated books is the
        # original ("L'étranger"), so "The Stranger" would never be found. author= keeps commentaries
        # and study guides by other authors from crowding the real work out of the results.
        try:
            data = await self._get("/search.json", {"q": title, "author": author,
                                                     "fields": SEARCH_FIELDS, "limit": SEARCH_LIMIT})
        except OpenLibraryError as e:
            logger.warning("Open Library search failed for '%s': %s", title, e)
            return []
        return [OpenLibraryDoc.model_validate(doc) for doc in data.get("docs", [])]

    async def find_book(self, pick: BookPick) -> Optional[OpenLibraryDoc]:
        """Search for Claude's pick and return the verified work, or None (logged) if nothing matches."""
        docs = await self.search(pick.title, pick.author)
        match = best_match(pick, docs)
        if match is None:
            logger.info("No verified match for '%s' by %s (%s); candidates: %s", pick.title, pick.author,
                        pick.year, [(d.title, d.author_name[:1], d.first_publish_year) for d in docs])
        return match

    async def get_description(self, work_id: str) -> Optional[str]:
        """The work's description, or None if it has none. Raises OpenLibraryError if it can't be fetched,
        so callers don't record "no description" for what was only a timeout."""
        data = await self._get(f"/works/{work_id}.json")
        return parse_description(data.get("description"))


@cache
def get_openlibrary_service() -> OpenLibraryService:
    """One shared instance, so the rate limit and connection pool cover every request."""
    return OpenLibraryService()
