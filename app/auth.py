"""
Who is making the request. The rest of the app only sees `CurrentUser`; how users sign in
(anonymously today, email/password or Google later) is Supabase's business, so adding sign-in
methods doesn't change anything that depends on `get_current_user`.
"""
import hashlib
import logging
import time
from functools import cache
from typing import Dict, Optional, Tuple

from fastapi import Depends, Header, HTTPException
from gotrue.errors import AuthError
from pydantic import BaseModel

from app.services.supabase_service import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)

CACHE_SECONDS = 60     # a verified token is trusted this long before asking Supabase again
CACHE_MAX_ENTRIES = 5000


class CurrentUser(BaseModel):
    id: str
    is_anonymous: bool


class AuthService:
    """Verifies Supabase access tokens. Asks Supabase Auth (works with the project's shared-secret
    signing, and notices signed-out or deleted users), with a short cache so a chat's requests don't
    each pay for a round trip. If the project moves to asymmetric signing keys, verification can
    become local in `verify` without touching any caller."""

    def __init__(self, supabase: SupabaseService) -> None:
        self._supabase = supabase
        self._cache: Dict[str, Tuple[CurrentUser, float]] = {}

    async def verify(self, token: str) -> Optional[CurrentUser]:
        key = hashlib.sha256(token.encode()).hexdigest()  # never keep raw tokens around
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        db = await self._supabase.db()
        try:
            response = await db.auth.get_user(token)
        except AuthError:
            return None  # expired, malformed, signed out, or a deleted user
        if response is None or response.user is None:
            return None

        user = CurrentUser(id=response.user.id, is_anonymous=bool(response.user.is_anonymous))
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                self._cache.clear()
        self._cache[key] = (user, now + CACHE_SECONDS)
        return user


@cache
def get_auth_service() -> AuthService:
    return AuthService(get_supabase_service())


async def get_current_user(
    authorization: Optional[str] = Header(None),
    auth: AuthService = Depends(get_auth_service),
) -> CurrentUser:
    """FastAPI dependency: the signed-in (possibly anonymous) user, or 401."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Sign-in required")
    try:
        user = await auth.verify(token)
    except Exception:
        logger.exception("Couldn't verify a sign-in token")
        raise HTTPException(status_code=503, detail="Sign-in check unavailable, please try again")
    if user is None:
        raise HTTPException(status_code=401, detail="Sign-in expired or invalid")
    return user
