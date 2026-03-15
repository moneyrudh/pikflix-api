import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.endpoints import movies, providers
from app.config import settings
from app.errors import register_error_handlers
from app.middleware import (
    RequestIDMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.rate_limit import limiter

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Quiet noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="PikFlix API",
    description="AI-powered movie recommendations via natural language queries",
    version="1.0.0",
)

# ── Rate limiting ────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Structured error responses ───────────────────────────────────────────────
register_error_handlers(app)

# ── Middleware (order matters — outermost first) ─────────────────────────────
# 1. Request ID: must be outermost so every response gets the header
app.add_middleware(RequestIDMiddleware)
# 2. Security headers: cheap, always-on
app.add_middleware(SecurityHeadersMiddleware)
# 3. Request logging: logs method/path/status/duration
app.add_middleware(RequestLoggingMiddleware)
# 4. CORS: use specific origins in production via ALLOWED_ORIGINS env var
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)

# ── Routes ───────────────────────────────────────────────────────────────────
app.include_router(movies.router, prefix="/api/movies", tags=["movies"])
app.include_router(providers.router, prefix="/api/providers", tags=["providers"])


@app.get("/health")
async def health_check():
    """
    Deep health check — verifies that external dependencies are reachable.

    Returns 200 with per-dependency status so container orchestrators and
    monitoring can distinguish between "app is up" and "app is up but
    Supabase is down".
    """
    checks: dict = {}

    # Supabase connectivity
    try:
        from app.services.supabase_service import SupabaseService
        svc = SupabaseService()
        svc.client.table("movies").select("id").limit(1).execute()
        checks["supabase"] = "ok"
    except Exception as e:
        checks["supabase"] = f"error: {type(e).__name__}"

    # TMDB API connectivity
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{settings.TMDB_BASE_URL}/configuration",
                headers={"Authorization": f"Bearer {settings.TMDB_READ_ACCESS_TOKEN}"},
            )
            checks["tmdb"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        checks["tmdb"] = f"error: {type(e).__name__}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"

    return {"status": overall, "checks": checks}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
