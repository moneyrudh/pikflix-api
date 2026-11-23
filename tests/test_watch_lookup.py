import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.api.endpoints.recommendations import _resolve_pick
from app.models import ContentType, ContentTypeMode, Movie, Show
from app.schemas import ContentRecommendation
from app.services.supabase_service import SupabaseService

NOW = datetime.now(timezone.utc)


def row(content_id: int, title: str, age_days: int = 0, videos_checked: bool = True, show: bool = False) -> Dict[str, Any]:
    updated = (NOW - timedelta(days=age_days)).isoformat()
    return {"id": content_id, ("name" if show else "title"): title, "last_updated": updated,
            "videos_checked_at": updated if videos_checked else None}


class FakeCache:
    is_fresh_content = staticmethod(SupabaseService.is_fresh_content)

    def __init__(self, by_title: Optional[Dict] = None, by_id: Optional[Dict] = None) -> None:
        self.by_title = by_title or {}
        self.by_id = by_id or {}
        self.saved: List[Tuple[int, ContentType]] = []

    async def get_content_by_title(self, title: str, year: Optional[int], content_type: ContentType):
        return self.by_title.get((title, content_type))

    async def get_content_by_id(self, content_id: int, content_type: ContentType):
        return self.by_id.get((content_id, content_type))

    async def save_content(self, items: List[Dict[str, Any]], content_type: ContentType) -> None:
        self.saved += [(item["id"], content_type) for item in items]

    async def save_providers(self, *args) -> None:
        pass


class FakeTMDB:
    def __init__(self, ids: Optional[Dict] = None, details: Optional[Dict] = None) -> None:
        self.ids = ids or {}          # title -> (id, type)
        self.details = details or {}  # (id, type) -> data
        self.searched: List[str] = []
        self.fetched: List[Tuple[int, ContentType]] = []

    async def find_id(self, title: str, year: Optional[int], content_type: ContentType):
        self.searched.append(title)
        return self.ids.get(title)

    async def get_details(self, content_id: int, content_type: ContentType):
        self.fetched.append((content_id, content_type))
        return self.details.get((content_id, content_type), {})

    async def get_content_providers(self, content_id: int, content_type: ContentType):
        return {"id": content_id, "results": {}}


def resolve(title: str, cache: FakeCache, tmdb: FakeTMDB, mode: ContentTypeMode = ContentTypeMode.MOVIE,
            content_type: ContentType = ContentType.MOVIE):
    async def run():
        rec = ContentRecommendation(title=title, year=1995, reason="r", content_type=content_type)
        result = await _resolve_pick(rec, mode, cache, tmdb)
        await asyncio.sleep(0)  # let the background cache writes run
        return result

    return asyncio.run(run())


def test_fresh_title_hit_makes_no_tmdb_calls():
    cache = FakeCache(by_title={("Star Wars", ContentType.MOVIE): row(11, "Star Wars")})
    tmdb = FakeTMDB()
    content_type, item = resolve("Star Wars", cache, tmdb)
    assert (content_type, item.id) == (ContentType.MOVIE, 11)
    assert tmdb.searched == [] and tmdb.fetched == [] and cache.saved == []


def test_differently_worded_title_hits_the_cache_by_id():
    # Live case: cached as "Star Wars"; Claude says "Star Wars: Episode IV - A New Hope".
    # One search finds the same id, and the cached row is used.
    full_title = "Star Wars: Episode IV - A New Hope"
    cache = FakeCache(by_id={(11, ContentType.MOVIE): row(11, "Star Wars")})
    tmdb = FakeTMDB(ids={full_title: (11, ContentType.MOVIE)})
    _, item = resolve(full_title, cache, tmdb)
    assert item.title == "Star Wars"
    assert tmdb.searched == [full_title] and tmdb.fetched == [] and cache.saved == []


def test_stale_title_hit_refreshes_by_id_without_searching():
    cache = FakeCache(by_title={("Heat", ContentType.MOVIE): row(949, "Heat", age_days=30)})
    tmdb = FakeTMDB(details={(949, ContentType.MOVIE): {"id": 949, "title": "Heat"}})
    _, item = resolve("Heat", cache, tmdb)
    assert item.id == 949
    assert tmdb.searched == [] and tmdb.fetched == [(949, ContentType.MOVIE)]
    assert cache.saved == [(949, ContentType.MOVIE)]


def test_rows_from_before_trailers_count_as_stale_once():
    cache = FakeCache(by_title={("Heat", ContentType.MOVIE): row(949, "Heat", videos_checked=False)})
    tmdb = FakeTMDB(details={(949, ContentType.MOVIE): {"id": 949, "title": "Heat"}})
    resolve("Heat", cache, tmdb)
    assert tmdb.fetched == [(949, ContentType.MOVIE)]


def test_full_miss_searches_fetches_and_caches():
    cache = FakeCache()
    tmdb = FakeTMDB(ids={"Ronin": (8195, ContentType.MOVIE)},
                    details={(8195, ContentType.MOVIE): {"id": 8195, "title": "Ronin"}})
    content_type, item = resolve("Ronin", cache, tmdb)
    assert isinstance(item, Movie) and item.id == 8195
    assert tmdb.searched == ["Ronin"] and tmdb.fetched == [(8195, ContentType.MOVIE)]
    assert cache.saved == [(8195, ContentType.MOVIE)]


def test_fallback_type_is_looked_up_and_cached_as_that_type():
    # Claude said movie, TMDB only has it as a show
    cache = FakeCache()
    tmdb = FakeTMDB(ids={"Chernobyl": (87108, ContentType.SHOW)},
                    details={(87108, ContentType.SHOW): {"id": 87108, "name": "Chernobyl"}})
    content_type, item = resolve("Chernobyl", cache, tmdb, mode=ContentTypeMode.BOTH)
    assert content_type == ContentType.SHOW and isinstance(item, Show)
    assert cache.saved == [(87108, ContentType.SHOW)]


def test_no_tmdb_match_resolves_to_nothing():
    assert resolve("Not A Real Film", FakeCache(), FakeTMDB()) is None
