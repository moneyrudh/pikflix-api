import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import httpx
import pytest
from anthropic.types import Message, TextBlock as APITextBlock, ToolUseBlock, Usage

from app.agent.blocks import BlockContext, BlockDrop, MediaBlock, QuestionBlock, TrailerBlock, parse_block, reply_schema
from app.agent.history import render_turns, shown_refs, window
from app.agent.loop import MAX_TOOL_CALLS, AgentTurn
from app.agent.streamer import ReplyStreamer
from app.agent.tools import ToolContext
from app.auth import CurrentUser, get_current_user
from app.main import app
from app.models import Book, ChatMessage, ChatSession, Movie, Trailer
from app.services.anthropic_service import get_anthropic_service
from app.services.chat_store import get_chat_store
from app.services.hydrate import get_hydrator
from tests.frontend import FRONTEND

DUNE = Movie(id=438631, title="Dune", trailer=Trailer(key="n9xhJrPXop4", name="Official Trailer"))
NO_TRAILER = Movie(id=1, title="No Trailer")
BOOK = Book(id="OL893414W", title="Dune")
ITEMS = {"movie:438631": DUNE, "movie:1": NO_TRAILER, "book:OL893414W": BOOK}


class FakeHydrator:
    async def hydrate(self, ref):
        return ITEMS.get(str(ref))


def block_ctx(seen=("movie:438631", "movie:1", "book:OL893414W")) -> BlockContext:
    return BlockContext(FakeHydrator(), set(seen))


def reply(*blocks: Dict[str, Any]) -> str:
    return json.dumps({"items": list(blocks)})


# --- Streamer

def stream_all(text: str, size: int):
    streamer, updates = ReplyStreamer(), []
    for i in range(0, len(text), size):
        updates += streamer.feed(text[i:i + size])
    return updates + streamer.close()


@pytest.mark.parametrize("size", [1, 5, 1000])
def test_streamer_emits_every_block_once_whatever_the_chunking(size):
    text = reply({"type": "text", "text": 'Intro "quoted" — é\n'}, {"type": "media", "ref": "movie:438631", "note": None},
                 {"type": "text", "text": "Outro"})
    updates = stream_all(text, size)
    assert [u.index for u in updates if u.kind == "start"] == [0, 1, 2]
    assert [u.index for u in updates if u.kind == "complete"] == [0, 1, 2]
    streamed = "".join(u.text for u in updates if u.kind == "text" and u.index == 0)
    assert streamed == 'Intro "quoted" — é\n'
    assert updates[-1].block == {"type": "text", "text": "Outro"}


def test_streamer_waits_for_a_complete_type_and_never_finishes_the_open_block():
    streamer = ReplyStreamer()
    assert streamer.feed('{"items": [{"type": "te') == []          # "te" isn't a type yet
    updates = streamer.feed('xt", "text": "Hel')
    assert [(u.kind, u.text) for u in updates] == [("start", None), ("text", "Hel")]
    assert [u.kind for u in streamer.feed('lo"}, {')] == ["text", "complete"]  # next block opened


def test_cut_off_reply_raises_on_close():
    streamer = ReplyStreamer()
    streamer.feed('{"items": [{"type": "text", "text": "Hi')
    with pytest.raises(ValueError):
        streamer.close()


# --- Blocks

def hydrate(block):
    return asyncio.run(block.hydrate(block_ctx()))


def test_media_block_hydrates_known_refs():
    out = hydrate(MediaBlock(type="media", ref="movie:438631", note="The 2021 film"))
    assert out["item"]["title"] == "Dune" and out["media_type"] == "movie" and out["note"] == "The 2021 film"


@pytest.mark.parametrize("block", [
    MediaBlock(type="media", ref="movie:603", note=None),         # never returned by a tool
    MediaBlock(type="media", ref="Dune", note=None),              # not a ref
    TrailerBlock(type="trailer", ref="movie:1"),                  # no trailer
    TrailerBlock(type="trailer", ref="book:OL893414W"),           # not watchable
])
def test_blocks_that_cant_be_shown_are_dropped(block):
    with pytest.raises(BlockDrop):
        hydrate(block)


def test_trailer_and_question_blocks():
    assert hydrate(TrailerBlock(type="trailer", ref="movie:438631"))["trailer"]["key"] == "n9xhJrPXop4"
    q = hydrate(QuestionBlock(type="question", question="Which one?", options=["a", "b", "c", "d", "e", "f"]))
    assert q["options"] == ["a", "b", "c", "d", "e"]


def test_unknown_or_invalid_raw_blocks_dont_parse():
    assert parse_block({"type": "poll"}) is None
    assert parse_block({"type": "media"}) is None
    assert isinstance(parse_block({"type": "media", "ref": "movie:1", "note": None}), MediaBlock)


def test_block_type_tags_are_enforced_in_the_schema():
    schema = reply_schema()
    assert {name: d["properties"]["type"]["enum"] for name, d in schema["$defs"].items()} == {
        "TextBlock": ["text"], "MediaBlock": ["media"], "TrailerBlock": ["trailer"], "QuestionBlock": ["question"]}
    assert "oneOf" not in json.dumps(schema) and "const" not in json.dumps(schema)


# --- Loop

def message(content, stop_reason: str) -> Message:
    return Message(id="m", type="message", role="assistant", model="test", content=content, stop_reason=stop_reason,
                   stop_sequence=None, usage=Usage(input_tokens=1, output_tokens=1))


class FakeStream:
    def __init__(self, final: Message) -> None:
        self.final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def __aiter__(self):
        return self._events()

    async def _events(self):
        for block in self.final.content:
            if block.type == "tool_use":
                yield SimpleNamespace(type="content_block_stop", content_block=block)
            else:
                for i in range(0, len(block.text), 7):
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=block.text[i:i + 7]))

    async def get_final_message(self):
        return self.final


class FakeClaude:
    def __init__(self, responses: List[Message]) -> None:
        self.responses = list(responses)
        self.requests: List[Dict[str, Any]] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        self.requests.append(request)
        return FakeStream(self.responses.pop(0))


def tool_call(i: int, ref: str = "movie:438631") -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=f"t{i}", name="get_details", input={"ref": ref})


def run_turn(responses: List[Message], seen=("movie:438631", "book:OL893414W")):
    claude = FakeClaude(responses)
    tools = ToolContext(None, None, None, None, seen={r: r for r in seen})

    async def fake_run_tool(name, args, ctx):
        return SimpleNamespace(text=f"{args['ref']} · details", is_error=False)

    import app.agent.loop as loop_module
    original = loop_module.run_tool
    loop_module.run_tool = fake_run_tool
    try:
        turn = AgentTurn(claude, "test", tools, BlockContext(FakeHydrator(), tools.seen))

        async def collect():
            return [e async for e in turn.run([{"role": "user", "content": [{"type": "text", "text": "hi"}]}])]

        return asyncio.run(collect()), turn, claude, tools
    finally:
        loop_module.run_tool = original


def test_a_tool_round_then_an_answer():
    answer = reply({"type": "text", "text": "Here it is."}, {"type": "media", "ref": "movie:438631", "note": None},
                   {"type": "media", "ref": "movie:603", "note": None})  # last one was never returned by a tool
    events, turn, claude, tools = run_turn([
        message([tool_call(1)], "tool_use"),
        message([APITextBlock(type="text", text=answer)], "end_turn"),
    ])
    kinds = [e.type for e in events]
    assert kinds[0] == "status" and kinds[-1] == "done"
    ends = {e.index: e.block for e in events if e.type == "block_end"}
    assert ends[0] == {"type": "text", "text": "Here it is."} and ends[1]["item"]["title"] == "Dune"
    assert [e.index for e in events if e.type == "block_drop"] == [2]
    assert "".join(e.text for e in events if e.type == "text_delta") == "Here it is."
    # Stored in complete units: tool call with its result, then the answer
    assert [(m["role"], [c["type"] for c in m["content"]]) for m in turn.new_messages] == [
        ("assistant", ["tool_use"]), ("user", ["tool_result"]), ("assistant", ["text"])]
    assert tools.shown == {"movie:438631": "movie:438631"}
    # Tools and the system prompt are marked for prompt caching
    assert claude.requests[0]["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_tool_budget_and_final_round_force_an_answer():
    calls = [tool_call(i) for i in range(MAX_TOOL_CALLS + 2)]
    events, turn, claude, _ = run_turn([
        message(calls, "tool_use"),
        message([APITextBlock(type="text", text=reply({"type": "text", "text": "Done."}))], "end_turn"),
    ])
    results = turn.new_messages[1]["content"]
    assert len(results) == len(calls)  # every call answered, the extras with an error
    assert sum(r["is_error"] for r in results) == 2
    assert claude.requests[1]["tool_choice"] == {"type": "none"}  # out of budget: answer now


def test_a_cut_off_answer_ends_with_an_error():
    events, turn, _, _ = run_turn([message([APITextBlock(type="text", text='{"items": [{"type": "te')], "max_tokens")])
    assert events[-1].type == "error" and turn.new_messages == []


# --- History

def msg(seq: int, role: str, content: List[Dict[str, Any]]) -> ChatMessage:
    return ChatMessage(seq=seq, role=role, content=content)


HISTORY = [
    msg(0, "user", [{"type": "text", "text": "first"}]),
    msg(1, "assistant", [{"type": "tool_use", "id": "t1", "name": "get_details", "input": {"ref": "movie:438631"}}]),
    msg(2, "user", [{"type": "tool_result", "tool_use_id": "t1", "content": "…"}]),
    msg(3, "assistant", [{"type": "text", "text": reply({"type": "media", "ref": "movie:438631", "note": None},
                                                        {"type": "trailer", "ref": "movie:438631"})}]),
    msg(4, "user", [{"type": "text", "text": "second"}]),
    msg(5, "assistant", [{"type": "text", "text": reply({"type": "media", "ref": "book:OL893414W", "note": None})}]),
]


def test_window_cuts_only_at_typed_user_messages():
    assert [m["role"] for m in window(HISTORY, exchanges=1)] == ["user", "assistant"]
    assert window(HISTORY, exchanges=1)[0]["content"][0]["text"] == "second"
    assert len(window(HISTORY, exchanges=20)) == len(HISTORY)


def test_shown_refs_in_order_without_repeats():
    assert shown_refs(HISTORY) == ["movie:438631", "book:OL893414W"]


def test_reopened_chat_renders_user_text_and_hydrated_answers():
    turns = asyncio.run(render_turns(HISTORY, block_ctx()))
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant"]
    assert [b["type"] for b in turns[1]["blocks"]] == ["media", "trailer"]
    assert turns[3]["blocks"][0]["item"]["title"] == "Dune"


# --- Endpoints

ALICE = CurrentUser(id="11111111-1111-1111-1111-111111111111", is_anonymous=True)
CHAT_ID = "aaaaaaaa-0000-0000-0000-000000000001"


class FakeStore:
    def __init__(self) -> None:
        self.messages: List[ChatMessage] = []
        self.touched: List[List[str]] = []
        self.session = ChatSession(id=CHAT_ID, owner_id=ALICE.id, title="t", seen_refs=["movie:438631"],
                                   created_at=datetime.now(timezone.utc), last_active_at=datetime.now(timezone.utc))

    async def create_session(self, owner_id, first_message):
        return self.session.model_copy(update={"title": first_message, "seen_refs": []})

    async def get_session(self, owner_id, session_id):
        return self.session if owner_id == ALICE.id and session_id == CHAT_ID else None

    async def load_messages(self, session_id):
        return list(self.messages)

    async def append_messages(self, session_id, messages):
        self.messages += messages

    async def touch(self, session_id, seen_refs):
        self.touched.append(seen_refs)


def post_chat(body: Dict[str, Any], store: FakeStore, signed_in: bool = True) -> httpx.Response:
    answer = reply({"type": "text", "text": "Hello!"})
    claude = FakeClaude([message([APITextBlock(type="text", text=answer)], "end_turn")])
    overrides = {get_chat_store: lambda: store, get_hydrator: FakeHydrator,
                 get_anthropic_service: lambda: SimpleNamespace(client=claude, model="test")}
    if signed_in:
        overrides[get_current_user] = lambda: ALICE
    app.dependency_overrides = overrides

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=FRONTEND) as client:
            response = await client.post("/api/chat", json=body)
        await asyncio.sleep(0.05)  # the background save
        return response

    try:
        return asyncio.run(run())
    finally:
        app.dependency_overrides = {}


def test_new_chat_streams_and_saves_the_turn():
    store = FakeStore()
    response = post_chat({"message": "  hi there  "}, store)
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0] == {"type": "session", "session_id": CHAT_ID, "title": "hi there"}
    assert events[-1] == {"type": "done"}
    assert [(m.seq, m.role) for m in store.messages] == [(0, "user"), (1, "assistant")]
    assert store.messages[0].content == [{"type": "text", "text": "hi there"}]
    assert store.touched  # last_active_at and seen_refs updated


def test_continuing_a_chat_appends_after_existing_messages():
    store = FakeStore()
    store.messages = HISTORY[:4]
    post_chat({"session_id": CHAT_ID, "message": "more"}, store)
    assert [m.seq for m in store.messages[4:]] == [4, 5]


@pytest.mark.parametrize("body, status", [
    ({"session_id": "bbbbbbbb-0000-0000-0000-000000000002", "message": "hi"}, 404),  # not mine
    ({"message": ""}, 422),
    ({"message": "x" * 2001}, 422),
    ({"message": "   "}, 422),
])
def test_bad_chat_requests(body, status):
    assert post_chat(body, FakeStore()).status_code == status


def test_chat_requires_sign_in():
    from app.auth import get_auth_service
    from tests.test_chats import FakeAuthClient, auth_service
    app.dependency_overrides = {get_auth_service: lambda: auth_service(FakeAuthClient({}))}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=FRONTEND) as client:
            return await client.post("/api/chat", json={"message": "hi"})

    try:
        assert asyncio.run(run()).status_code == 401
    finally:
        app.dependency_overrides = {}


def test_get_chat_reopens_it():
    store = FakeStore()
    store.messages = HISTORY
    app.dependency_overrides = {get_chat_store: lambda: store, get_hydrator: FakeHydrator, get_current_user: lambda: ALICE}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=FRONTEND) as client:
            return await client.get(f"/api/chats/{CHAT_ID}"), await client.get("/api/chats/bbbbbbbb-0000-0000-0000-000000000002")

    try:
        mine, other = asyncio.run(run())
    finally:
        app.dependency_overrides = {}
    assert mine.status_code == 200 and [t["role"] for t in mine.json()["turns"]] == ["user", "assistant", "user", "assistant"]
    assert other.status_code == 404


def test_turn_limits_refuse_before_anything_is_saved(usage_limits):
    usage_limits.refuse = 12
    store = FakeStore()
    response = post_chat({"message": "hi"}, store)
    assert response.status_code == 429 and response.headers["retry-after"] == "12"
    assert "12 seconds" in response.json()["detail"]
    assert store.messages == []  # no chat created, no message saved


def test_guests_get_the_lower_daily_cap(usage_limits):
    from app.config import CHAT_TURNS_PER_DAY_GUEST, CHAT_TURNS_PER_MINUTE
    post_chat({"message": "hi"}, FakeStore())
    assert usage_limits == [("chat", ALICE.id, CHAT_TURNS_PER_MINUTE, CHAT_TURNS_PER_DAY_GUEST)]  # ALICE is anonymous


def test_a_missing_chat_is_404_without_using_a_turn(usage_limits):
    response = post_chat({"session_id": "bbbbbbbb-0000-0000-0000-000000000002", "message": "hi"}, FakeStore())
    assert response.status_code == 404 and usage_limits == []


def test_limit_messages():
    from app.services.rate_limit import limit_message
    assert limit_message(5 * 3600, "today's chat limit for guests") == \
        "You've reached today's chat limit for guests. Try again in about 5 hours."
    assert limit_message(1, "today's search limit") == "You're going a bit fast. Try again in 1 second."
