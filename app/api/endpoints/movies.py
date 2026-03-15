import asyncio
import json
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.auth import require_api_key
from app.models import UserQuery, Movie
from app.rate_limit import limiter
from app.services.anthropic_service import AnthropicService
from app.services.supabase_service import SupabaseService
from app.services.tmdb_service import TMDBService

logger = logging.getLogger(__name__)

router = APIRouter()


def get_anthropic_service():
    return AnthropicService()


def get_supabase_service():
    return SupabaseService()


def get_tmdb_service():
    return TMDBService()


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


async def _safe_background(coro, description: str):
    """Run a coroutine as a background task with error logging instead of silent swallowing."""
    try:
        await coro
    except Exception:
        logger.exception("Background task failed: %s", description)


@router.post("/recommendations", dependencies=[Depends(require_api_key)])
async def get_recommendations_stream(
    query: UserQuery,
    anthropic_service: AnthropicService = Depends(get_anthropic_service),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service),
):
    async def generate():
        yield json.dumps({"type": "init", "query": query.query}) + "\n"

        async for movie_rec in anthropic_service.get_movie_recommendations(query.query):
            db_result = await supabase_service.get_movies_by_titles([movie_rec])

            found_movies = db_result["found_movies"]
            to_fetch = db_result["to_fetch"]

            movie_data = None

            if found_movies:
                movie_data = found_movies[0]
            elif to_fetch:
                fetched_movies = await tmdb_service.fetch_movie_data(to_fetch)
                if fetched_movies:
                    movie_data = fetched_movies[0]
                    asyncio.create_task(
                        _safe_background(
                            supabase_service.save_movies([movie_data]),
                            f"save movie {movie_data.get('title')}",
                        )
                    )
                    provider_data = await tmdb_service.get_movie_providers(movie_data["id"])
                    if provider_data and "results" in provider_data:
                        asyncio.create_task(
                            _safe_background(
                                supabase_service.save_movie_providers(movie_data["id"], provider_data),
                                f"save providers for movie {movie_data['id']}",
                            )
                        )

            if movie_data:
                try:
                    movie_data["reason"] = movie_rec.get("reason", "")
                    movie = Movie.model_validate(movie_data)

                    yield json.dumps({
                        "type": "movie",
                        "data": movie.model_dump()
                    }, default=json_serial) + "\n"
                except Exception:
                    logger.exception("Error processing movie: %s", movie_data.get("title"))

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson"
    )
