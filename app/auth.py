from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from app.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str = Security(_api_key_header)) -> str:
    """
    FastAPI dependency that validates the X-API-Key header.

    If PIKFLIX_API_KEY is not configured, authentication is disabled (open access).
    This lets the app run without auth in local development while enforcing it
    in production by simply setting the env var.
    """
    if settings.API_KEY is None:
        # Auth not configured — allow all requests (dev mode)
        return ""
    if not api_key or api_key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return api_key
