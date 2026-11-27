import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.blocks import BlockContext
from app.agent.events import SessionEvent
from app.agent.history import shown_refs, window
from app.agent.loop import AgentTurn
from app.agent.tools import ToolContext
from app.auth import CurrentUser, get_current_user
from app.config import CHAT_TURNS_PER_DAY_GUEST, CHAT_TURNS_PER_DAY_USER, CHAT_TURNS_PER_MINUTE
from app.models import ChatMessage
from app.services.anthropic_service import AnthropicService, get_anthropic_service
from app.services.chat_store import ChatStore, get_chat_store
from app.services.hydrate import Hydrator, get_hydrator
from app.services.rate_limit import UsageLimits, get_usage_limits
from app.services.openlibrary_service import OpenLibraryService, get_openlibrary_service
from app.services.semantic_scholar_service import SemanticScholarService, get_semantic_scholar_service
from app.services.supabase_service import SupabaseService, get_supabase_service
from app.services.tmdb_service import TMDBService, get_tmdb_service

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_MESSAGE_CHARS = 2000
_background: Set["asyncio.Task[None]"] = set()  # keeps fire-and-forget saves alive until done


class ChatRequest(BaseModel):
    session_id: Optional[UUID] = Field(None, description="Omit to start a new chat")
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


async def _save_turn(store: ChatStore, session_id: str, first_seq: int, messages: List[Dict[str, Any]], seen: List[str]) -> None:
    try:
        await store.append_messages(session_id, [ChatMessage(seq=first_seq + i, role=m["role"], content=m["content"])
                                                 for i, m in enumerate(messages)])
        await store.touch(session_id, seen)
    except Exception:
        logger.exception("Couldn't save a turn of chat %s", session_id)


@router.post("")
async def chat(
    body: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    store: ChatStore = Depends(get_chat_store),
    anthropic_service: AnthropicService = Depends(get_anthropic_service),
    tmdb: TMDBService = Depends(get_tmdb_service),
    openlibrary: OpenLibraryService = Depends(get_openlibrary_service),
    semantic_scholar: SemanticScholarService = Depends(get_semantic_scholar_service),
    supabase: SupabaseService = Depends(get_supabase_service),
    hydrator: Hydrator = Depends(get_hydrator),
    limits: UsageLimits = Depends(get_usage_limits),
) -> StreamingResponse:
    """One agent turn, streamed as NDJSON (see app/agent/events.py). Starts a chat when session_id is omitted."""
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Message is empty")
    session = None
    if body.session_id:
        session = await store.get_session(user.id, str(body.session_id))
        if session is None:
            raise HTTPException(status_code=404, detail="Chat not found")

    # Checked (and the turn counted) before anything is saved or Claude is called
    await limits.enforce(
        "chat", user.id, CHAT_TURNS_PER_MINUTE,
        CHAT_TURNS_PER_DAY_GUEST if user.is_anonymous else CHAT_TURNS_PER_DAY_USER,
        "today's chat limit for guests" if user.is_anonymous else "today's chat limit",
    )

    if session is not None:
        stored = await store.load_messages(session.id)
    else:
        session = await store.create_session(user.id, text)
        stored = []

    # The user's message is saved before Claude is called, so it's never lost
    next_seq = stored[-1].seq + 1 if stored else 0
    user_message = ChatMessage(seq=next_seq, role="user", content=[{"type": "text", "text": text}])
    await store.append_messages(session.id, [user_message])

    tools = ToolContext(tmdb, openlibrary, semantic_scholar, supabase,
                        seen={ref: ref for ref in session.seen_refs},
                        shown={ref: ref for ref in shown_refs(stored)})
    turn = AgentTurn(anthropic_service.client, anthropic_service.model, tools, BlockContext(hydrator, tools.seen))

    async def generate() -> AsyncGenerator[str, None]:
        yield SessionEvent(session_id=session.id, title=session.title).model_dump_json() + "\n"
        try:
            async for event in turn.run(window(stored + [user_message])):
                yield event.model_dump_json() + "\n"
        finally:
            # Also runs when the client disconnects; a task, so the save isn't cancelled along with the stream
            task = asyncio.create_task(_save_turn(store, session.id, next_seq + 1, turn.new_messages, list(tools.seen)))
            _background.add(task)
            task.add_done_callback(_background.discard)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
