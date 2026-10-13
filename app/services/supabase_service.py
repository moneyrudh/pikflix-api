import asyncio
import logging
from functools import cache
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone, date

logger = logging.getLogger(__name__)
from supabase import AsyncClient, AsyncClientOptions, acreate_client
from app.config import SUPABASE_URL, SUPABASE_KEY, CACHE_DURATION, PROVIDER_CACHE_DURATION
from app.models import ContentType, FetchRequest, CacheResult
from app.schemas import ContentRecommendation

class SupabaseService:
    def __init__(self):
        self.schema = "pikflix"
        # The async client is created on first use (creation is itself async). The sync
        # client blocked the event loop on every query, stalling concurrent requests.
        self._client: Optional[AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def _db(self) -> AsyncClient:
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

    async def get_content_by_titles(self, recommendations: List[ContentRecommendation], content_type: ContentType) -> CacheResult:
        """
        Check if content exists in the database and determine which need to be fetched/refreshed.
        Branches column names based on content_type (title/release_date vs name/first_air_date).
        """
        db = await self._db()
        table = self._table_for(content_type)
        title_col = "name" if content_type == ContentType.SHOW else "title"
        date_col = "first_air_date" if content_type == ContentType.SHOW else "release_date"

        found = []
        to_fetch = []
        cache_expiry = datetime.now(timezone.utc) - timedelta(hours=CACHE_DURATION)

        for rec in recommendations:
            query = db.table(table).select("*").eq(title_col, rec.title)

            if rec.year:
                query = query.filter(date_col, "gte", f"{rec.year}-01-01")
                query = query.filter(date_col, "lte", f"{rec.year}-12-31")

            result = await query.execute()

            if result.data and len(result.data) > 0:
                item = result.data[0]
                last_updated = datetime.fromisoformat(item['last_updated'])

                # Rows cached before trailers existed are refetched once to backfill them
                if last_updated < cache_expiry or item.get('videos_checked_at') is None:
                    to_fetch.append(FetchRequest(
                        title=rec.title,
                        year=rec.year,
                        reason=rec.reason,
                        id=item['id']
                    ))
                else:
                    item['reason'] = rec.reason or ''
                    found.append(item)
            else:
                to_fetch.append(FetchRequest(
                    title=rec.title,
                    year=rec.year,
                    reason=rec.reason
                ))

        return CacheResult(found=found, to_fetch=to_fetch)

    async def save_content(self, items: List[Dict[str, Any]], content_type: ContentType) -> None:
        db = await self._db()
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
        db = await self._db()
        query = db.table("providers").select("*").eq("content_id", content_id).eq("content_type", content_type.value)
        result = await query.execute()

        if not result.data or len(result.data) == 0:
            return None

        provider_data = result.data[0]

        # Streaming catalogs change often; treat stale rows as a miss so callers refetch from TMDB.
        last_updated = provider_data.get("last_updated")
        cache_expiry = datetime.now(timezone.utc) - timedelta(hours=PROVIDER_CACHE_DURATION)
        if not last_updated or datetime.fromisoformat(last_updated) < cache_expiry:
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
            db = await self._db()
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

    async def close(self) -> None:
        if self._client is not None:
            await self._client.postgrest.aclose()


@cache
def get_supabase_service() -> SupabaseService:
    return SupabaseService()
