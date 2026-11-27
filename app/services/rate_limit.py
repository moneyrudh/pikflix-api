import asyncio
import hashlib
import hmac
import logging
from functools import cache
from typing import Tuple

from fastapi import HTTPException

from app.config import BACKEND_SHARED_SECRET
from app.services.supabase_service import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)


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


class UsageLimits:
    """Per-minute and per-day limits on requests that cost Claude tokens, counted in Supabase (see the
    take_rate_limit SQL function) so they hold across restarts and server instances."""

    def __init__(self, supabase: SupabaseService) -> None:
        self._supabase = supabase

    async def take(self, bucket: str, key: str, per_minute: int, per_day: int) -> Tuple[bool, int]:
        """Check the limits and, if allowed, count this request. Returns (allowed, seconds to wait)."""
        db = await self._supabase.db()
        result = await db.rpc("take_rate_limit", {
            "p_bucket": bucket, "p_key": key, "p_per_minute": per_minute, "p_per_day": per_day}).execute()
        row = result.data[0]
        return bool(row["allowed"]), int(row["retry_after"])

    async def enforce(self, bucket: str, key: str, per_minute: int, per_day: int, what: str) -> None:
        """Count the request, or refuse it with a 429 that says how long to wait."""
        allowed, retry_after = await self.take(bucket, key, per_minute, per_day)
        if not allowed:
            logger.info("Rate limited %s for %s (retry in %ss)", bucket, key[:12], retry_after)
            raise HTTPException(status_code=429, detail=limit_message(retry_after, what),
                                headers={"Retry-After": str(retry_after)})


def limit_message(retry_after: int, what: str) -> str:
    """User-facing text for a refusal; `what` names the daily limit, e.g. "today's search limit"."""
    if retry_after <= 60:
        return f"You're going a bit fast. Try again in {retry_after} second{'s' if retry_after != 1 else ''}."
    hours = max(1, round(retry_after / 3600))
    return f"You've reached {what}. Try again in about {hours} hour{'s' if hours != 1 else ''}."


def visitor_key(client_ip: str) -> str:
    """A stable key for a visitor without storing their IP: keyed hash with the shared secret."""
    return hmac.new((BACKEND_SHARED_SECRET or "").encode(), client_ip.encode(), hashlib.sha256).hexdigest()


@cache
def get_usage_limits() -> UsageLimits:
    return UsageLimits(get_supabase_service())
