"""
Who is making the request. The rest of the app only sees `CurrentUser`; how users sign in
(anonymously today, email/password or Google later) is Supabase's business, so adding sign-in
methods doesn't change anything that depends on `get_current_user`.
"""
import hashlib
import hmac
import logging
import time
from functools import cache
from typing import Awaitable, Callable, Dict, Optional, Tuple

from fastapi import Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from gotrue.errors import AuthError
from pydantic import BaseModel

from app.config import BACKEND_SHARED_SECRET
from app.services.rate_limit import visitor_key
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


# --- The frontend proxy -----------------------------------------------------------------------------------
# The backend is only meant to be called by the frontend's server-side proxy routes, which add the shared
# secret and the visitor's IP. Requests without the secret are refused, so nobody can skip the frontend
# (or claim another visitor's IP) by calling the backend directly.

PROXY_SECRET_HEADER = "X-Pikflix-Proxy-Secret"
CLIENT_IP_HEADER = "X-Pikflix-Client-Ip"


async def require_frontend(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """HTTP middleware: /api requests must carry the shared secret."""
    if request.url.path.startswith("/api/") and request.method != "OPTIONS":
        sent = request.headers.get(PROXY_SECRET_HEADER, "")
        if not hmac.compare_digest(sent.encode(), (BACKEND_SHARED_SECRET or "").encode()):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


def get_visitor_key(client_ip: Optional[str] = Header(None, alias=CLIENT_IP_HEADER)) -> str:
    """Who to count a search against: the visitor's IP as forwarded by the frontend, hashed."""
    return visitor_key(client_ip or "unknown")
