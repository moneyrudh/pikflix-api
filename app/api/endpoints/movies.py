import json
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
from app.models import UserQuery, Movie, Show, ContentType, ContentTypeMode, FetchRequest
from app.services.anthropic_service import AnthropicService
from app.services.supabase_service import SupabaseService
from app.services.tmdb_service import TMDBService
import asyncio
from datetime import date, datetime

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


@router.post("/recommendations")
async def get_recommendations_stream(
    query: UserQuery,
    anthropic_service: AnthropicService = Depends(get_anthropic_service),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service)
):
    request_mode = query.content_type
    is_both = request_mode == ContentTypeMode.BOTH

    async def generate():
        yield json.dumps({"type": "init", "query": query.query, "content_type": request_mode.value}) + "\n"

        async for rec in anthropic_service.get_recommendations(query.query, query.history, request_mode):
            # Determine content_type for this specific recommendation
            if is_both:
                # Use Claude's per-item classification
                rec_type = ContentType(rec.content_type)
            else:
                # Explicit mode — ignore Claude's classification, use request type
                rec_type = ContentType(request_mode.value)

            model_class = Show if rec_type == ContentType.SHOW else Movie

            # Check Supabase cache first
            cache_result = await supabase_service.get_content_by_titles([rec], rec_type)

            item_data = None
            resolved_type = rec_type

            if cache_result.found:
                item_data = cache_result.found[0]
            elif cache_result.to_fetch:
                fetch_item = cache_result.to_fetch[0]

                if fetch_item.id:
                    # Direct fetch by cached ID
                    fetched = await tmdb_service.fetch_content_data([fetch_item], rec_type)
                    if fetched:
                        item_data = fetched[0]
                else:
                    # Search TMDB with fallback to other type
                    data, resolved_type = await tmdb_service.search_content(
                        fetch_item.title, fetch_item.year, rec_type
                    )
                    if data:
                        item_data = data
                        item_data['reason'] = fetch_item.reason or ''
                        model_class = Show if resolved_type == ContentType.SHOW else Movie

                if item_data:
                    asyncio.create_task(supabase_service.save_content([item_data], resolved_type))
                    provider_data = await tmdb_service.get_content_providers(item_data["id"], resolved_type)
                    if provider_data and "results" in provider_data:
                        asyncio.create_task(supabase_service.save_providers(item_data["id"], resolved_type, provider_data))

            if item_data:
                try:
                    item_data["reason"] = rec.reason or ""
                    validated = model_class.model_validate(item_data)
                    yield json.dumps({
                        "type": "content",
                        "content_type": resolved_type.value,
                        "data": validated.model_dump()
                    }, default=json_serial) + "\n"
                except Exception as e:
                    logger.error("Error processing %s '%s': %s", resolved_type.value, rec.title, e)

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson"
    )
