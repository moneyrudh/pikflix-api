from fastapi import APIRouter, Depends, HTTPException
from app.models import ProviderRequest, ProviderResponse
from app.services.supabase_service import SupabaseService, get_supabase_service
from app.services.tmdb_service import TMDBService, get_tmdb_service
import asyncio

router = APIRouter()


@router.post("/", response_model=ProviderResponse)
async def get_providers(
    request: ProviderRequest,
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service)
) -> ProviderResponse:
    if not request.region:
        raise HTTPException(status_code=400, detail="Region parameter is required")

    # Try database first
    db_providers = await supabase_service.get_providers(request.content_id, request.content_type, request.region)

    if db_providers:
        return ProviderResponse.model_validate(db_providers)

    # Fetch from TMDB
    tmdb_providers = await tmdb_service.get_content_providers(request.content_id, request.content_type)

    if tmdb_providers and "results" in tmdb_providers:
        asyncio.create_task(supabase_service.save_providers(request.content_id, request.content_type, tmdb_providers))

    results = tmdb_providers.get("results", {}) if tmdb_providers else {}
    region_data = results.get(request.region, {})

    return ProviderResponse(id=request.content_id, results={request.region: region_data})
