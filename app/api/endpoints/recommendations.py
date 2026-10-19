import json
import logging
from typing import Optional
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
from app.models import UserQuery, Movie, Show, ContentType, ContentTypeMode
from app.schemas import ContentRecommendation
from app.services.anthropic_service import AnthropicService, get_anthropic_service
from app.services.supabase_service import SupabaseService, get_supabase_service
from app.services.tmdb_service import TMDBService, get_tmdb_service
import asyncio
from datetime import date, datetime

router = APIRouter()


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


async def _cache_providers(tmdb_service: TMDBService, supabase_service: SupabaseService, content_id: int, content_type: ContentType):
    """Fetch and cache providers in the background."""
    try:
        provider_data = await tmdb_service.get_content_providers(content_id, content_type)
        if provider_data and "results" in provider_data:
            await supabase_service.save_providers(content_id, content_type, provider_data)
    except Exception as e:
        logger.error("Error caching providers for %s ID %s: %s", content_type.value, content_id, e)


async def _resolve_pick(
    rec: ContentRecommendation,
    request_mode: ContentTypeMode,
    supabase_service: SupabaseService,
    tmdb_service: TMDBService,
) -> Optional[str]:
    """Resolve one recommendation (cache first, TMDB on miss) into an NDJSON line, or None if not found."""
    if request_mode == ContentTypeMode.BOTH:
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
                model_class = Show if resolved_type == ContentType.SHOW else Movie

    if not item_data:
        return None

    line = None
    try:
        validated = model_class.model_validate(item_data)
        # `reason` describes this pick, not the title, so it rides on the event rather than in `data`
        line = json.dumps({
            "type": "content",
            "content_type": resolved_type.value,
            "reason": rec.reason or None,
            "data": validated.model_dump()
        }, default=json_serial) + "\n"
    except Exception as e:
        logger.error("Error processing %s '%s': %s", resolved_type.value, rec.title, e)

    # Background: cache content and providers (only if freshly fetched)
    if cache_result.to_fetch:
        asyncio.create_task(supabase_service.save_content([item_data], resolved_type))
        asyncio.create_task(_cache_providers(tmdb_service, supabase_service, item_data["id"], resolved_type))

    return line


@router.post("/")
async def get_recommendations_stream(
    query: UserQuery,
    anthropic_service: AnthropicService = Depends(get_anthropic_service),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service)
):
    request_mode = query.content_type

    async def generate():
        yield json.dumps({"type": "init", "query": query.query, "content_type": request_mode.value}) + "\n"

        # Each pick is resolved in its own task as soon as Claude emits it, so a slow cache/TMDB
        # lookup doesn't hold up later picks. Results are streamed in completion order.
        lines: asyncio.Queue[Optional[str]] = asyncio.Queue()

        async def resolve(rec: ContentRecommendation) -> None:
            try:
                line = await _resolve_pick(rec, request_mode, supabase_service, tmdb_service)
            except Exception as e:
                logger.error("Error resolving '%s': %s", rec.title, e)
                return
            if line:
                await lines.put(line)

        async def produce() -> None:
            try:
                async with asyncio.TaskGroup() as tg:
                    async for rec in anthropic_service.get_recommendations(query.query, query.history, request_mode, query.web_search):
                        tg.create_task(resolve(rec))
            except Exception as e:
                logger.error("Error producing recommendations: %s", e)
            finally:
                await lines.put(None)  # end of stream

        producer = asyncio.create_task(produce())
        try:
            while (line := await lines.get()) is not None:
                yield line
        finally:
            # Client disconnected or stream finished: stop any lookups still in flight
            producer.cancel()

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson"
    )
