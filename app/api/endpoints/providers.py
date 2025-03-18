from fastapi import APIRouter, Depends, HTTPException
from app.models import ProviderRequest, ProviderResponse
from app.services.supabase_service import SupabaseService
from app.services.tmdb_service import TMDBService
import asyncio
from typing import Dict, Any

router = APIRouter()


def get_supabase_service():
    return SupabaseService()


def get_tmdb_service():
    return TMDBService()


@router.post("/", response_model=ProviderResponse)
async def get_movie_providers(
    request: ProviderRequest,
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service)
) -> ProviderResponse:
    """
    Get streaming providers for a specific movie and region
    
    This endpoint checks if the data exists in Supabase first.
    If not found, it fetches from TMDB API and stores it for future use.
    Always returns only the specified region's data to the UI.
    """
    # Ensure we have a region specified (required parameter)
    if not request.region:
        raise HTTPException(status_code=400, detail="Region parameter is required")
        
    # Try to get providers from database first
    db_providers = await supabase_service.get_movie_providers(request.movie_id, request.region)
    
    if db_providers:
        # Data found in database - already filtered to just the requested region
        return db_providers
    
    # Data not in database, fetch from TMDB
    tmdb_providers = await tmdb_service.get_movie_providers(request.movie_id)
    
    # Save complete provider data to database asynchronously
    if tmdb_providers and "results" in tmdb_providers:
        asyncio.create_task(supabase_service.save_movie_providers(request.movie_id, tmdb_providers))
    
    # Extract just the region-specific data to return to UI
    results = tmdb_providers.get("results", {}) if tmdb_providers else {}
    region_data = results.get(request.region, {})
    
    # Return only the requested region's data
    return {
        "id": request.movie_id,
        "results": {
            request.region: region_data
        }
    }