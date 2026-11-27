import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional

import httpx

from app.main import app
from app.models import Author, Book, OpenLibraryDoc
from app.schemas import BookPick
from app.services.anthropic_service import get_anthropic_service
from app.services.openlibrary_service import OpenLibraryError, get_openlibrary_service, lookup_key
from app.services.supabase_service import get_supabase_service
from app.services.tmdb_service import get_tmdb_service
from tests.frontend import FRONTEND

# Claude's 12 picks, in the order it emits them, with each Open Library lookup's delay (seconds).
# "Cached" is a cache hit; the two "Unverifiable" picks have no matching work.
LOOKUP_DELAYS = {"Unverifiable A": 0.0, "Cached": None, "Unverifiable B": 0.0,
                 **{f"Book {i}": 0.02 * i for i in range(1, 10)}}


class FakeAnthropic:
    def __init__(self) -> None:
        self.stream_ran_to_end = False

    async def get_book_picks(self, *args, stats=None, **kwargs) -> AsyncGenerator[BookPick, None]:
        for title in LOOKUP_DELAYS:
            yield BookPick(title=title, author="Some Author", year=2000, reason=f"why {title}")
        await asyncio.sleep(1.0)  # Claude still writing; should be cancelled once 9 books are verified
        self.stream_ran_to_end = True


class FakeOpenLibrary:
    def __init__(self) -> None:
        self.started: List[str] = []
        self.finished: List[str] = []

    async def find_book(self, pick: BookPick) -> Optional[OpenLibraryDoc]:
        self.started.append(pick.title)
        await asyncio.sleep(LOOKUP_DELAYS[pick.title])
        self.finished.append(pick.title)
        if pick.title.startswith("Unverifiable"):
            return None
        return OpenLibraryDoc(key=f"/works/OL{len(self.finished)}W", title=pick.title,
                              author_name=["Some Author"], first_publish_year=2000)


class FakeSupabase:
    def __init__(self) -> None:
        self.saved: List[str] = []

    async def get_book_by_lookup_key(self, key: str) -> Optional[Book]:
        if key == lookup_key("Cached", "Some Author"):
            return Book(id="OL42W", title="Cached", authors=[Author(name="Some Author")])
        return None

    async def save_book(self, book: Book, key: str) -> None:
        self.saved.append(book.title)


def run_book_search() -> tuple[List[dict], FakeAnthropic, FakeOpenLibrary, FakeSupabase]:
    anthropic, openlibrary, supabase = FakeAnthropic(), FakeOpenLibrary(), FakeSupabase()
    app.dependency_overrides = {
        get_anthropic_service: lambda: anthropic,
        get_openlibrary_service: lambda: openlibrary,
        get_supabase_service: lambda: supabase,
        get_tmdb_service: object,
    }

    async def post() -> List[dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=FRONTEND) as client:
            response = await client.post("/api/recommendations/", json={"query": "q", "media_types": ["book"]})
        return [json.loads(line) for line in response.text.splitlines()]

    try:
        return asyncio.run(post()), anthropic, openlibrary, supabase
    finally:
        app.dependency_overrides = {}


def test_book_search_stops_at_nine_verified_books():
    events, anthropic, openlibrary, supabase = run_book_search()

    assert events[0] == {"type": "init", "query": "q", "media_types": ["book"], "content_type": None}
    items = [e for e in events if e["type"] == "item"]
    assert [e["data"]["title"] for e in items] == ["Cached"] + [f"Book {i}" for i in range(1, 9)]
    assert all(e["media_type"] == "book" for e in items)
    assert items[0]["reason"] == "why Cached"
    assert not [e for e in events if e["type"] == "content"]  # the legacy event is watch-only
    assert events[-1] == {"type": "done", "count": 9, "dropped": 2}

    assert "Cached" not in openlibrary.started            # cache hit: no Open Library call
    assert "Book 9" in openlibrary.started and "Book 9" not in openlibrary.finished  # cancelled after the 9th
    assert not anthropic.stream_ran_to_end                 # Claude's stream closed early


def test_verified_books_are_cached_in_the_background():
    _, _, _, supabase = run_book_search()
    assert set(supabase.saved) >= {f"Book {i}" for i in range(1, 9)}
    assert "Cached" not in supabase.saved


class FakeBookStore:
    def __init__(self) -> None:
        self.book = Book(id="OL893414W", title="Dune")
        self.saved_descriptions: List[Optional[str]] = []

    async def get_book(self, book_id: str) -> Optional[Book]:
        return self.book if book_id == self.book.id else None

    async def save_book_description(self, book_id: str, description: Optional[str]) -> None:
        self.saved_descriptions.append(description)
        self.book.description = description
        self.book.description_checked_at = datetime.now(timezone.utc)


class FakeDescriptions:
    def __init__(self, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    async def get_description(self, work_id: str) -> Optional[str]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise OpenLibraryError("/works/OL893414W.json: ReadTimeout")
        return "A desert planet."


def get_books(*paths: str, fail_first: bool = False) -> tuple[List[httpx.Response], FakeBookStore, FakeDescriptions]:
    store, descriptions = FakeBookStore(), FakeDescriptions(fail_first)
    app.dependency_overrides = {get_supabase_service: lambda: store, get_openlibrary_service: lambda: descriptions}

    async def run() -> List[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=FRONTEND) as client:
            return [await client.get(path) for path in paths]

    try:
        return asyncio.run(run()), store, descriptions
    finally:
        app.dependency_overrides = {}


def test_book_details_fetch_the_description_once():
    (first, second), store, descriptions = get_books("/api/books/OL893414W", "/api/books/OL893414W")
    assert first.status_code == second.status_code == 200
    assert first.json()["description"] == second.json()["description"] == "A desert planet."
    assert descriptions.calls == 1 and store.saved_descriptions == ["A desert planet."]


def test_book_details_reject_unknown_and_malformed_ids():
    (unknown, malformed), _, descriptions = get_books("/api/books/OL1W", "/api/books/..%2Fsearch.json")
    assert unknown.status_code == 404
    assert malformed.status_code in (404, 422)  # never reaches Open Library
    assert descriptions.calls == 0


def test_description_timeout_returns_the_book_and_retries_next_time():
    (first, second), store, descriptions = get_books("/api/books/OL893414W", "/api/books/OL893414W", fail_first=True)
    assert first.status_code == 200 and first.json()["description"] is None
    assert first.json()["description_checked_at"] is None   # not recorded as "has no description"
    assert second.json()["description"] == "A desert planet."
    assert descriptions.calls == 2 and store.saved_descriptions == ["A desert planet."]
