from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
import json
from app.models import UserQuery, MovieRecommendationResponse, Movie
from app.services.anthropic_service import AnthropicService
from app.services.supabase_service import SupabaseService
from app.services.tmdb_service import TMDBService
from typing import List
import asyncio
from datetime import date, datetime

router = APIRouter()


def get_anthropic_service():
    return AnthropicService()


def get_supabase_service():
    return SupabaseService()


def get_tmdb_service():
    return TMDBService()


# Add this function to serialize dates for JSON
def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@router.post("/recommendations")
async def get_recommendations_stream(
    query: UserQuery,
    anthropic_service: AnthropicService = Depends(get_anthropic_service),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service)
):
    async def generate():
        # Stream header to establish the format
        yield json.dumps({"type": "init", "query": query.query}) + "\n"
        
        async for movie_rec in anthropic_service.get_movie_recommendations(query.query):
            # Check if movie exists in Supabase
            db_result = await supabase_service.get_movies_by_titles([movie_rec])
            
            found_movies = db_result["found_movies"]
            to_fetch = db_result["to_fetch"]
            
            movie_data = None
            
            # If movie is in database and fresh
            if found_movies:
                movie_data = found_movies[0]
            # If movie needs to be fetched
            elif to_fetch:
                fetched_movies = await tmdb_service.fetch_movie_data(to_fetch)
                if fetched_movies:
                    movie_data = fetched_movies[0]
                    # Save to database asynchronously (don't wait for it)
                    asyncio.create_task(supabase_service.save_movies([movie_data]))
            
            # If we have movie data, convert to Pydantic model and yield
            if movie_data:
                try:
                    # Add the "reason" from the recommendation
                    movie_data["reason"] = movie_rec.get("reason", "")
                    
                    # Convert to Pydantic model
                    movie = Movie.model_validate(movie_data)
                    
                    # Use custom serializer for the date objects
                    yield json.dumps({
                        "type": "movie", 
                        "data": movie.model_dump()
                    }, default=json_serial) + "\n"
                except Exception as e:
                    print(f"Error processing movie: {str(e)}")
    
    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson"
    )