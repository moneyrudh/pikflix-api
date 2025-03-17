from fastapi import APIRouter, Depends, HTTPException
from app.models import UserQuery, MovieRecommendationResponse, Movie
from app.services.anthropic_service import AnthropicService
from app.services.supabase_service import SupabaseService
from app.services.tmdb_service import TMDBService
from typing import List

router = APIRouter()


def get_anthropic_service():
    return AnthropicService()


def get_supabase_service():
    return SupabaseService()


def get_tmdb_service():
    return TMDBService()


@router.post("/recommendations", response_model=MovieRecommendationResponse)
async def get_recommendations(
    query: UserQuery,
    anthropic_service: AnthropicService = Depends(get_anthropic_service),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service)
):
    # 1. Get movie recommendations from Anthropic API
    movie_recommendations = await anthropic_service.get_movie_recommendations(query.query)
    
    if not movie_recommendations:
        raise HTTPException(status_code=500, detail="Failed to get movie recommendations")
    
    # 2. Check which movies exist in the database and which need to be fetched
    db_result = await supabase_service.get_movies_by_titles(movie_recommendations)
    
    found_movies = db_result["found_movies"]
    to_fetch = db_result["to_fetch"]
    
    # 3. Fetch missing/stale movies from TMDB
    new_movies = []
    if to_fetch:
        new_movies = await tmdb_service.fetch_movie_data(to_fetch)
        
        # 4. Save the fetched movies to the database
        if new_movies:
            await supabase_service.save_movies(new_movies)
    
    # 5. Combine all movies and return
    all_movies = found_movies + new_movies
    
    # Convert to Pydantic models
    movies = [Movie.model_validate(movie) for movie in all_movies]
    
    # Sort to match original recommendation order if possible
    if len(movies) > 0:
        # Create a map of title to position in the original recommendations
        rec_order = {rec["title"].lower(): i for i, rec in enumerate(movie_recommendations)}
        
        # Sort based on the original order
        movies.sort(key=lambda x: rec_order.get(x.title.lower(), 999))  
    
    return MovieRecommendationResponse(
        recommendations=movies,
        query=query.query
    )