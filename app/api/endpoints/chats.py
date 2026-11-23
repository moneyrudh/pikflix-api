from typing import Any, Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.agent.blocks import BlockContext
from app.agent.history import render_turns
from app.auth import CurrentUser, get_current_user
from app.models import ChatSessionSummary
from app.services.chat_store import ChatStore, get_chat_store
from app.services.hydrate import Hydrator, get_hydrator

router = APIRouter()


@router.get("", response_model=List[ChatSessionSummary])
async def list_chats(
    user: CurrentUser = Depends(get_current_user),
    store: ChatStore = Depends(get_chat_store),
) -> List[ChatSessionSummary]:
    """The signed-in user's chats, most recently active first (the sidebar)."""
    return await store.list_sessions(user.id)


@router.get("/{session_id}")
async def get_chat(
    session_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    store: ChatStore = Depends(get_chat_store),
    hydrator: Hydrator = Depends(get_hydrator),
) -> Dict[str, Any]:
    """A stored chat, rendered as the stream would have rendered it (to reopen it)."""
    session = await store.get_session(user.id, str(session_id))
    if session is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    messages = await store.load_messages(session.id)
    turns = await render_turns(messages, BlockContext(hydrator, set(session.seen_refs)))
    return {"id": session.id, "title": session.title, "turns": turns}


@router.delete("/{session_id}", status_code=204)
async def delete_chat(
    session_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    store: ChatStore = Depends(get_chat_store),
) -> Response:
    # Someone else's chat looks exactly like a missing one
    if not await store.delete_session(user.id, str(session_id)):
        raise HTTPException(status_code=404, detail="Chat not found")
    return Response(status_code=204)
