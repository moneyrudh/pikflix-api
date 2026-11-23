import logging
from datetime import datetime, timezone
from functools import cache
from typing import List, Optional

from app.config import ANONYMOUS_CHAT_RETENTION_DAYS, CHAT_TITLE_MAX_CHARS
from app.models import ChatMessage, ChatSession, ChatSessionSummary
from app.services.supabase_service import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)


def chat_title(first_message: str) -> str:
    """The sidebar title: the first message on one line, trimmed. No extra model call."""
    text = " ".join(first_message.split())
    return text if len(text) <= CHAT_TITLE_MAX_CHARS else text[:CHAT_TITLE_MAX_CHARS - 1].rstrip() + "…"


class ChatStore:
    """Agent chat persistence. Every session lookup is scoped to its owner; messages are append-only."""

    def __init__(self, supabase: SupabaseService) -> None:
        self._supabase = supabase

    async def create_session(self, owner_id: str, first_message: str) -> ChatSession:
        db = await self._supabase.db()
        result = await db.table("chat_sessions").insert(
            {"owner_id": owner_id, "title": chat_title(first_message)}).execute()
        return ChatSession.model_validate(result.data[0])

    async def get_session(self, owner_id: str, session_id: str) -> Optional[ChatSession]:
        """The session if it exists AND belongs to this user; otherwise None (callers answer 404 either way)."""
        db = await self._supabase.db()
        result = await (db.table("chat_sessions").select("*")
                        .eq("id", session_id).eq("owner_id", owner_id).limit(1).execute())
        return ChatSession.model_validate(result.data[0]) if result.data else None

    async def list_sessions(self, owner_id: str, limit: int = 50) -> List[ChatSessionSummary]:
        db = await self._supabase.db()
        result = await (db.table("chat_sessions").select("id,title,last_active_at")
                        .eq("owner_id", owner_id).order("last_active_at", desc=True).limit(limit).execute())
        return [ChatSessionSummary.model_validate(row) for row in result.data]

    async def load_messages(self, session_id: str) -> List[ChatMessage]:
        db = await self._supabase.db()
        result = await (db.table("chat_messages").select("seq,role,content")
                        .eq("session_id", session_id).order("seq").execute())
        return [ChatMessage.model_validate(row) for row in result.data]

    async def append_messages(self, session_id: str, messages: List[ChatMessage]) -> None:
        """Insert in one request. (session_id, seq) is the primary key, so a duplicate turn fails loudly."""
        if not messages:
            return
        db = await self._supabase.db()
        await db.table("chat_messages").insert(
            [{"session_id": session_id, **m.model_dump()} for m in messages]).execute()

    async def touch(self, session_id: str, seen_refs: List[str]) -> None:
        """Mark the session active and store the refs tools have returned so far."""
        db = await self._supabase.db()
        await db.table("chat_sessions").update({
            "last_active_at": datetime.now(timezone.utc).isoformat(),
            "seen_refs": sorted(set(seen_refs)),
        }).eq("id", session_id).execute()

    async def delete_session(self, owner_id: str, session_id: str) -> bool:
        db = await self._supabase.db()
        result = await (db.table("chat_sessions").delete()
                        .eq("id", session_id).eq("owner_id", owner_id).execute())
        return bool(result.data)

    async def delete_expired_anonymous(self, days: int = ANONYMOUS_CHAT_RETENTION_DAYS) -> int:
        """Delete inactive chats of users who are still anonymous (see the SQL function's comment)."""
        db = await self._supabase.db()
        result = await db.rpc("delete_expired_anonymous_chats", {"max_age": f"{days} days"}).execute()
        return int(result.data or 0)


@cache
def get_chat_store() -> ChatStore:
    return ChatStore(get_supabase_service())
