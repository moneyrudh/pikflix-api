import asyncio
import logging
from functools import cache
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone, date

logger = logging.getLogger(__name__)
from supabase import AsyncClient, AsyncClientOptions, acreate_client
from app.config import SUPABASE_URL, SUPABASE_KEY, CACHE_DURATION, PROVIDER_CACHE_DURATION, BOOK_CACHE_DURATION
from app.models import Book, ContentType

def _fresh_after(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _is_fresh(row: Dict[str, Any], hours: int) -> bool:
    """The one staleness check every cached table uses: `last_updated` within the last `hours`."""
    last_updated = row.get("last_updated")
    if not last_updated:
        return False
    return datetime.fromisoformat(last_updated) >= _fresh_after(hours)


class SupabaseService:
    def __init__(self):
        self.schema = "pikflix"
        # The async client is created on first use (creation is itself async). The sync
        # client blocked the event loop on every query, stalling concurrent requests.
        self._client: Optional[AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def db(self) -> AsyncClient:
        if self._client is None:
            assert SUPABASE_URL and SUPABASE_KEY  # config.py exits at startup when these are missing
            async with self._client_lock:
                if self._client is None:
                    self._client = await acreate_client(
                        SUPABASE_URL,
                        SUPABASE_KEY,
                        options=AsyncClientOptions(schema=self.schema)
                    )
        return self._client

    @staticmethod
    def _table_for(content_type: ContentType) -> str:
        return "shows" if content_type == ContentType.SHOW else "movies"

    async def get_content_by_title(self, title: str, year: Optional[int], content_type: ContentType) -> Optional[Dict[str, Any]]:
        """The cached row for an exact title (and release year, when given), fresh or not.
        Branches column names on content_type (title/release_date vs name/first_air_date)."""
        db = await self.db()
        title_col = "name" if content_type == ContentType.SHOW else "title"
        date_col = "first_air_date" if content_type == ContentType.SHOW else "release_date"
        query = db.table(self._table_for(content_type)).select("*").eq(title_col, title)
        if year:
            query = query.filter(date_col, "gte", f"{year}-01-01").filter(date_col, "lte", f"{year}-12-31")
        result = await query.limit(1).execute()
        return result.data[0] if result.data else None

    async def get_content_by_id(self, content_id: int, content_type: ContentType) -> Optional[Dict[str, Any]]:
        """The cached row for a TMDB id, fresh or not."""
        db = await self.db()
        result = await db.table(self._table_for(content_type)).select("*").eq("id", content_id).limit(1).execute()
        return result.data[0] if result.data else None

    @staticmethod
    def is_fresh_content(row: Dict[str, Any]) -> bool:
        """Usable as is. Rows cached before trailers existed count as stale once, to backfill them."""
        return _is_fresh(row, CACHE_DURATION) and row.get("videos_checked_at") is not None

    async def save_content(self, items: List[Dict[str, Any]], content_type: ContentType) -> None:
        db = await self.db()
        table = self._table_for(content_type)
        for item in items:
            try:
                item['last_updated'] = datetime.now(timezone.utc).isoformat()
                prepared = self._prepare_for_db(item, content_type)
                label = prepared.get('title') or prepared.get('name', 'unknown')
                logger.info("Saving %s: %s", content_type.value, label)
                await db.table(table).upsert(prepared).execute()
            except Exception as e:
                label = item.get('title') or item.get('name', 'unknown')
                logger.error("Error saving %s %s: %s (%s)", content_type.value, label, e, e.__class__.__name__)

    def _prepare_for_db(self, item: Dict[str, Any], content_type: ContentType) -> Dict[str, Any]:
        copy = item.copy()

        # Remove transient fields not in DB
        for field in ('reason',):
            copy.pop(field, None)

        # Convert date objects to ISO strings
        date_fields = ['release_date'] if content_type == ContentType.MOVIE else ['first_air_date', 'last_air_date']
        for field in date_fields:
            if field in copy and isinstance(copy[field], date):
                copy[field] = copy[field].isoformat()

        return copy

    async def get_providers(self, content_id: int, content_type: ContentType, region: Optional[str] = None) -> Optional[Dict[str, Any]]:
        db = await self.db()
        query = db.table("providers").select("*").eq("content_id", content_id).eq("content_type", content_type.value)
        result = await query.execute()

        if not result.data or len(result.data) == 0:
            return None

        provider_data = result.data[0]

        # Streaming catalogs change often; treat stale rows as a miss so callers refetch from TMDB.
        if not _is_fresh(provider_data, PROVIDER_CACHE_DURATION):
            return None

        results = provider_data.get("results") or {}

        if region:
            return {
                "id": content_id,
                "results": {
                    region: results.get(region, {})
                }
            }

        return {
            "id": content_id,
            "results": results
        }

    async def save_providers(self, content_id: int, content_type: ContentType, provider_data: Dict[str, Any]) -> None:
        try:
            db = await self.db()
            data = {
                "content_id": content_id,
                "content_type": content_type.value,
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "results": provider_data.get("results", {})
            }

            query = db.table("providers").select("content_id").eq("content_id", content_id).eq("content_type", content_type.value)
            result = await query.execute()

            if result.data and len(result.data) > 0:
                await db.table("providers").update(data).eq("content_id", content_id).eq("content_type", content_type.value).execute()
            else:
                await db.table("providers").insert(data).execute()

            logger.info("Saved providers for %s ID: %s", content_type.value, content_id)
        except Exception as e:
            logger.error("Error saving providers for %s ID %s: %s (%s)", content_type.value, content_id, e, e.__class__.__name__)

    async def get_book_by_lookup_key(self, key: str) -> Optional[Book]:
        """A cached book matching a pick's lookup key (see openlibrary_service.lookup_key), if still fresh."""
        db = await self.db()
        result = await (db.table("books").select("*").eq("lookup_key", key)
                        .gte("last_updated", _fresh_after(BOOK_CACHE_DURATION).isoformat())
                        .order("edition_count", desc=True).limit(1).execute())
        return Book.model_validate(result.data[0]) if result.data else None

    async def get_book(self, book_id: str) -> Optional[Book]:
        db = await self.db()
        result = await db.table("books").select("*").eq("id", book_id).limit(1).execute()
        return Book.model_validate(result.data[0]) if result.data else None

    async def save_book(self, book: Book, key: str) -> None:
        """Upsert a book found by search. Search results never carry the description, so those columns
        are left out: a refresh keeps a description fetched earlier."""
        try:
            db = await self.db()
            row = book.model_dump(mode="json", exclude={"last_updated", "description", "description_checked_at"})
            row["lookup_key"] = key
            row["last_updated"] = datetime.now(timezone.utc).isoformat()
            await db.table("books").upsert(row).execute()
            logger.info("Saved book: %s", book.title)
        except Exception as e:
            logger.error("Error saving book %s: %s (%s)", book.title, e, e.__class__.__name__)

    async def save_book_description(self, book_id: str, description: Optional[str]) -> None:
        try:
            db = await self.db()
            await db.table("books").update({
                "description": description,
                "description_checked_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", book_id).execute()
        except Exception as e:
            logger.error("Error saving description for book %s: %s (%s)", book_id, e, e.__class__.__name__)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.postgrest.aclose()


@cache
def get_supabase_service() -> SupabaseService:
    return SupabaseService()
