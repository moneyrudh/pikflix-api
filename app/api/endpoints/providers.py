import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_api_key
from app.models import ProviderRequest, ProviderResponse
from app.services.supabase_service import SupabaseService
from app.services.tmdb_service import TMDBService

logger = logging.getLogger(__name__)

router = APIRouter()


def get_supabase_service():
    return SupabaseService()


def get_tmdb_service():
    return TMDBService()


async def _safe_background(coro, description: str):
    """Run a coroutine as a background task with error logging instead of silent swallowing."""
    try:
        await coro
    except Exception:
        logger.exception("Background task failed: %s", description)


@router.post("/", response_model=ProviderResponse, dependencies=[Depends(require_api_key)])
async def get_movie_providers(
    request: ProviderRequest,
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> ProviderResponse:
    """
    Get streaming providers for a specific movie and region.

    Checks Supabase cache first. If not found, fetches from TMDB API and stores it.
    Always returns only the specified region's data.
    """
    if not request.region:
        raise HTTPException(status_code=400, detail="Region parameter is required")

    db_providers = await supabase_service.get_movie_providers(request.movie_id, request.region)

    if db_providers:
        return db_providers

    tmdb_providers = await tmdb_service.get_movie_providers(request.movie_id)

    if tmdb_providers and "results" in tmdb_providers:
        asyncio.create_task(
            _safe_background(
                supabase_service.save_movie_providers(request.movie_id, tmdb_providers),
                f"save providers for movie {request.movie_id}",
            )
        )

    results = tmdb_providers.get("results", {}) if tmdb_providers else {}
    region_data = results.get(request.region, {})

    return {
        "id": request.movie_id,
        "results": {
            request.region: region_data
        },
    }
