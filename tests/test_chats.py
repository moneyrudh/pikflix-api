import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Optional

import httpx
import pytest
from fastapi import HTTPException
from gotrue.errors import AuthApiError

from app.auth import AuthService, CurrentUser, get_auth_service, get_current_user
from app.main import app
from app.models import ChatSessionSummary
from app.services.chat_store import chat_title, get_chat_store
from tests.frontend import FRONTEND

ALICE = CurrentUser(id="11111111-1111-1111-1111-111111111111", is_anonymous=True)
ALICES_CHAT = "aaaaaaaa-0000-0000-0000-000000000001"
BOBS_CHAT = "bbbbbbbb-0000-0000-0000-000000000002"


# --- Auth

class FakeAuthClient:
    def __init__(self, users: dict, fail: bool = False) -> None:
        self.users = users  # token -> user id
        self.fail = fail
        self.calls = 0

    async def get_user(self, token: str):
        self.calls += 1
        if self.fail:
            raise ConnectionError("auth down")
        if token not in self.users:
            raise AuthApiError("invalid JWT", 401, "bad_jwt")
        return SimpleNamespace(user=SimpleNamespace(id=self.users[token], is_anonymous=True))


def auth_service(client: FakeAuthClient) -> AuthService:
    async def db():
        return SimpleNamespace(auth=client)

    return AuthService(SimpleNamespace(db=db))


def current_user(header: Optional[str], service: AuthService) -> CurrentUser:
    return asyncio.run(get_current_user(authorization=header, auth=service))


@pytest.mark.parametrize("header", [None, "", "Token abc", "Bearer "])
def test_requests_without_a_bearer_token_are_rejected(header):
    with pytest.raises(HTTPException) as e:
        current_user(header, auth_service(FakeAuthClient({})))
    assert e.value.status_code == 401


def test_an_invalid_token_is_rejected():
    with pytest.raises(HTTPException) as e:
        current_user("Bearer forged", auth_service(FakeAuthClient({"good": ALICE.id})))
    assert e.value.status_code == 401


def test_a_valid_token_resolves_to_the_user_and_is_cached():
    client = FakeAuthClient({"good": ALICE.id})
    service = auth_service(client)
    assert current_user("Bearer good", service) == ALICE
    assert current_user("bearer good", service) == ALICE
    assert client.calls == 1  # the second request didn't ask Supabase again


def test_an_auth_outage_is_a_503_not_a_crash():
    with pytest.raises(HTTPException) as e:
        current_user("Bearer good", auth_service(FakeAuthClient({}, fail=True)))
    assert e.value.status_code == 503


# --- Titles

def test_chat_title_is_the_first_message_on_one_line_and_trimmed():
    assert chat_title("  movies like\n\nArrival  ") == "movies like Arrival"
    long = chat_title("word " * 50)
    assert len(long) == 80 and long.endswith("…")


# --- Endpoints

class FakeStore:
    def __init__(self) -> None:
        self.owned = {ALICES_CHAT: ALICE.id, BOBS_CHAT: "someone-else"}

    async def list_sessions(self, owner_id: str, limit: int = 50) -> List[ChatSessionSummary]:
        now = datetime.now(timezone.utc)
        return [ChatSessionSummary(id=sid, title="t", last_active_at=now)
                for sid, owner in self.owned.items() if owner == owner_id]

    async def delete_session(self, owner_id: str, session_id: str) -> bool:
        if self.owned.get(session_id) != owner_id:
            return False
        del self.owned[session_id]
        return True


def call(method: str, path: str, signed_in: bool = True) -> httpx.Response:
    store = FakeStore()
    overrides = {get_chat_store: lambda: store}
    if signed_in:
        overrides[get_current_user] = lambda: ALICE
    else:
        overrides[get_auth_service] = lambda: auth_service(FakeAuthClient({}))
    app.dependency_overrides = overrides

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=FRONTEND) as client:
            return await client.request(method, path)

    try:
        return asyncio.run(run())
    finally:
        app.dependency_overrides = {}


def test_list_shows_only_my_chats():
    response = call("GET", "/api/chats")
    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == [ALICES_CHAT]


def test_delete_my_chat():
    assert call("DELETE", f"/api/chats/{ALICES_CHAT}").status_code == 204


def test_someone_elses_chat_looks_missing():
    assert call("DELETE", f"/api/chats/{BOBS_CHAT}").status_code == 404


def test_malformed_chat_ids_never_reach_the_database():
    assert call("DELETE", "/api/chats/not-a-uuid").status_code == 422


def test_chats_require_sign_in():
    assert call("GET", "/api/chats", signed_in=False).status_code == 401
