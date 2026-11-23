from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.auth import CurrentUser, get_current_user
from app.models import ChatSessionSummary
from app.services.chat_store import ChatStore, get_chat_store

router = APIRouter()


@router.get("", response_model=List[ChatSessionSummary])
async def list_chats(
    user: CurrentUser = Depends(get_current_user),
    store: ChatStore = Depends(get_chat_store),
) -> List[ChatSessionSummary]:
    """The signed-in user's chats, most recently active first (the sidebar)."""
    return await store.list_sessions(user.id)


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
