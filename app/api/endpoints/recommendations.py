import logging
from typing import AsyncGenerator, Optional, Tuple, Union
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
from app.config import BOOK_RESULTS_SHOWN
from app.models import (
    UserQuery, Book, Movie, Show, ContentType, ContentTypeMode, MediaType, Shelf,
    InitEvent, ContentEvent, ItemEvent, DoneEvent, ErrorEvent,
)
from app.schemas import BookPick, ContentRecommendation
from app.services.anthropic_service import AnthropicService, get_anthropic_service
from app.services.llm_stream import ParseStats
from app.services.openlibrary_service import OpenLibraryService, get_openlibrary_service, display_title, lookup_key
from app.services.supabase_service import SupabaseService, get_supabase_service
from app.services.tmdb_service import TMDBService, get_tmdb_service
import asyncio

router = APIRouter()

LLM_ERROR_MESSAGE = "Couldn't get recommendations right now. Please try again."


class _EnoughResults(Exception):
    """Raised by the task that sends the last result needed; cancels the rest of the search."""


def _line(event: Union[InitEvent, ContentEvent, ItemEvent, DoneEvent, ErrorEvent]) -> str:
    return event.model_dump_json() + "\n"


async def _resolve_book(pick: BookPick, openlibrary: OpenLibraryService, supabase_service: SupabaseService) -> Optional[Book]:
    """Cache first, then Open Library search + verification. None when no result is verifiably the book."""
    cached = await supabase_service.get_book_by_lookup_key(lookup_key(pick.title, pick.author))
    if cached is not None:
        return cached
    doc = await openlibrary.find_book(pick)
    if doc is None:
        return None
    # Show the title that matched (a translated work's English edition title, not "L'étranger")
    book = doc.to_book(title=display_title(pick, doc))
    # Keyed by that title and the catalogue's author, so later picks worded differently still hit the cache
    asyncio.create_task(supabase_service.save_book(
        book, lookup_key(book.title, doc.author_name[0] if doc.author_name else pick.author)))
    return book


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
) -> Optional[Tuple[ContentType, Union[Movie, Show]]]:
    """Resolve one recommendation (cache first, TMDB on miss), or None if not found."""
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

    validated: Optional[Union[Movie, Show]] = None
    try:
        validated = model_class.model_validate(item_data)
    except Exception as e:
        logger.error("Error processing %s '%s': %s", resolved_type.value, rec.title, e)

    # Background: cache content and providers (only if freshly fetched)
    if cache_result.to_fetch:
        asyncio.create_task(supabase_service.save_content([item_data], resolved_type))
        asyncio.create_task(_cache_providers(tmdb_service, supabase_service, item_data["id"], resolved_type))

    return (resolved_type, validated) if validated else None


@router.post("/")
async def get_recommendations_stream(
    query: UserQuery,
    anthropic_service: AnthropicService = Depends(get_anthropic_service),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    tmdb_service: TMDBService = Depends(get_tmdb_service),
    openlibrary: OpenLibraryService = Depends(get_openlibrary_service),
):
    reading = query.shelf == Shelf.READ
    request_mode = None if reading else query.watch_mode
    # Books ask Claude for extra picks and stop once enough are verified; watch shows every pick
    limit = BOOK_RESULTS_SHOWN if reading else None

    async def generate() -> AsyncGenerator[str, None]:
        yield _line(InitEvent(query=query.query, media_types=query.media_types, content_type=request_mode))

        # Each pick is resolved in its own task as soon as Claude emits it, so a slow cache/TMDB
        # lookup doesn't hold up later picks. Results are streamed in completion order.
        lines: asyncio.Queue[Optional[str]] = asyncio.Queue()
        sent = dropped = 0
        parse_stats = ParseStats()  # picks Claude emitted that failed validation also count as dropped

        async def resolve(pick: Union[ContentRecommendation, BookPick]) -> None:
            nonlocal sent, dropped
            item: Union[Movie, Show, Book, None] = None
            legacy_type: Optional[ContentType] = None  # watch results also go out as the legacy `content` event
            try:
                if isinstance(pick, BookPick):
                    item = await _resolve_book(pick, openlibrary, supabase_service)
                    media_type = MediaType.BOOK
                else:
                    assert request_mode is not None
                    resolved = await _resolve_pick(pick, request_mode, supabase_service, tmdb_service)
                    if resolved is not None:
                        legacy_type, item = resolved
                        media_type = MediaType(legacy_type.value)
            except Exception:
                logger.exception("Error resolving '%s'", pick.title)
            if item is None:
                dropped += 1
                return
            if limit is not None and sent >= limit:
                return  # finished just after the last needed result; not a failure
            sent += 1
            reason = pick.reason or None
            await lines.put(_line(ItemEvent(media_type=media_type, reason=reason, data=item)))
            if legacy_type is not None and not isinstance(item, Book):
                await lines.put(_line(ContentEvent(content_type=legacy_type, reason=reason, data=item)))
            if limit is not None and sent >= limit:
                raise _EnoughResults

        picks: AsyncGenerator[Union[ContentRecommendation, BookPick], None]
        if reading:
            picks = anthropic_service.get_book_picks(query.query, query.history, query.web_search, stats=parse_stats)
        else:
            assert request_mode is not None
            picks = anthropic_service.get_recommendations(query.query, query.history, request_mode, query.web_search, stats=parse_stats)

        async def produce() -> None:
            llm_error: Optional[Exception] = None
            try:
                try:
                    async with asyncio.TaskGroup() as tg:
                        try:
                            async for pick in picks:
                                tg.create_task(resolve(pick))
                        except Exception as e:
                            # Caught inside the group so picks already in flight still finish and get sent
                            llm_error = e
                except* _EnoughResults:
                    pass  # enough results: Claude's stream and the remaining lookups were cancelled
                if llm_error:
                    logger.error("Error streaming recommendations from Claude", exc_info=llm_error)
                    await lines.put(_line(ErrorEvent(message=LLM_ERROR_MESSAGE)))
                else:
                    await lines.put(_line(DoneEvent(count=sent, dropped=dropped + parse_stats.skipped)))
            except Exception:
                logger.exception("Error producing recommendations")
                await lines.put(_line(ErrorEvent(message=LLM_ERROR_MESSAGE)))
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
