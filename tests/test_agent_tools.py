import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.agent.tools import TOOLS, ToolContext, run_tool, tool_definitions, tool_status
from app.models import Author, Book, ContentType, MediaRef, Movie, OpenLibraryDoc, Paper, RefType, S2Paper
from app.services.hydrate import Hydrator
from app.services.semantic_scholar_service import SemanticScholarError

NOW = datetime.now(timezone.utc)

DUNE_CREDITS = {
    "id": 438631, "title": "Dune", "release_date": "2021-09-15", "overview": "Paul Atreides…", "runtime": 155,
    "genres": [{"id": 878, "name": "Science Fiction"}],
    "credits": {
        "cast": [{"id": 1190668, "name": "Timothée Chalamet"}],
        "crew": [
            {"id": 137427, "name": "Denis Villeneuve", "job": "Director", "department": "Directing"},
            {"id": 12506, "name": "Frank Herbert", "job": "Novel", "department": "Writing"},
            {"id": 137427, "name": "Denis Villeneuve", "job": "Screenplay", "department": "Writing"},
        ],
    },
    "keywords": {"keywords": [{"id": 818, "name": "based on novel or book"}]},
}

HERBERT = {
    "id": 12506, "name": "Frank Herbert", "known_for_department": "Writing", "birthday": "1920-10-08",
    "deathday": "1986-02-11", "biography": "American science-fiction author.",
    "combined_credits": {
        "cast": [],
        "crew": [
            {"id": 841, "media_type": "movie", "title": "Dune", "release_date": "1984-12-14", "job": "Novel",
             "department": "Writing", "vote_count": 3000},
            {"id": 438631, "media_type": "movie", "title": "Dune", "release_date": "2021-09-15", "job": "Novel",
             "department": "Writing", "vote_count": 12000},
            {"id": 438631, "media_type": "movie", "title": "Dune", "release_date": "2021-09-15", "job": "Characters",
             "department": "Writing", "vote_count": 12000},
            {"id": 90228, "media_type": "tv", "name": "Dune: Prophecy", "first_air_date": "2024-11-17", "job": "Novel",
             "department": "Writing", "vote_count": 500},
        ],
    },
}


class FakeTMDB:
    def __init__(self) -> None:
        self.search_limits: List[int] = []
        self.details_fetched: List[int] = []

    async def search_movies(self, query, year=None, limit=1):
        self.search_limits.append(limit)
        return [{"id": 438631, "title": "Dune", "release_date": "2021-09-15", "overview": "Paul Atreides…"}]

    async def search_shows(self, query, year=None, limit=1):
        return []

    async def get_credits_and_keywords(self, content_id, content_type):
        return DUNE_CREDITS if content_id == 438631 else None

    async def search_person(self, name):
        return {"id": 12506} if "Herbert" in name else None

    async def get_person(self, person_id):
        return HERBERT if person_id == 12506 else None

    async def get_recommendations(self, content_id, content_type, limit=8):
        return [{"id": 841, "title": "Dune", "release_date": "1984-12-14", "overview": "Spice."}]

    async def get_details(self, content_id, content_type):
        self.details_fetched.append(content_id)
        return {"id": content_id, "title": "Fresh from TMDB"}


class FakeOpenLibrary:
    async def search_any(self, query, limit=8):
        return [OpenLibraryDoc(key="/works/OL893414W", title="Dune", author_name=["Frank Herbert"],
                               first_publish_year=1965, subject=["Science fiction"])]

    async def get_description(self, work_id):
        return "Set on the desert planet Arrakis."


class FakeS2:
    def __init__(self, down: bool = False) -> None:
        self.down = down

    async def search(self, query, limit=20):
        if self.down:
            raise SemanticScholarError("HTTP 429")
        return [S2Paper(paperId="p1", title="Attention Is All You Need", year=2017, venue="NeurIPS", citationCount=100)]

    async def get_paper(self, paper_id):
        return S2Paper(paperId=paper_id, title="Fetched Paper") if paper_id == "fetch-me" else None


class FakeSupabase:
    def __init__(self) -> None:
        self.books: Dict[str, Book] = {}
        self.papers: Dict[str, Paper] = {}
        self.content: Dict[int, Dict[str, Any]] = {}
        self.saved_content: List[int] = []
        self.descriptions: Dict[str, Optional[str]] = {}

    async def save_book(self, book, key):
        self.books[book.id] = book

    async def get_book(self, book_id):
        return self.books.get(book_id)

    async def save_book_description(self, book_id, description):
        self.descriptions[book_id] = description

    async def save_papers(self, papers):
        self.papers.update({p.id: p for p in papers})

    async def get_paper(self, paper_id):
        return self.papers.get(paper_id)

    async def get_content_by_id(self, content_id, content_type):
        return self.content.get(content_id)

    @staticmethod
    def is_fresh_content(row):
        return row.get("fresh", False)

    async def save_content(self, items, content_type):
        self.saved_content += [i["id"] for i in items]


def context(s2_down: bool = False) -> ToolContext:
    return ToolContext(FakeTMDB(), FakeOpenLibrary(), FakeS2(s2_down), FakeSupabase())


def call(name: str, args: Dict[str, Any], ctx: Optional[ToolContext] = None):
    ctx = ctx or context()

    async def run():
        out = await run_tool(name, args, ctx)
        await asyncio.sleep(0)  # background cache writes
        return out

    return asyncio.run(run()), ctx


# --- Refs

@pytest.mark.parametrize("value, expected", [
    ("movie:603", (RefType.MOVIE, "603")), ("book:OL893414W", (RefType.BOOK, "OL893414W")),
    (" person:12506 ", (RefType.PERSON, "12506")), ("paper:abc:def", (RefType.PAPER, "abc:def")),
])
def test_refs_parse(value, expected):
    ref = MediaRef.parse(value)
    assert (ref.type, ref.id) == expected and str(ref) == value.strip()


@pytest.mark.parametrize("value", ["603", "movie:", "film:603", ""])
def test_bad_refs_are_rejected(value):
    with pytest.raises(ValueError):
        MediaRef.parse(value)


# --- Registry

def test_every_tool_is_a_strict_closed_schema():
    definitions = tool_definitions()
    assert [d["name"] for d in definitions] == [t.name for t in TOOLS]
    for d in definitions:
        assert d["strict"] is True and d["input_schema"]["additionalProperties"] is False


def test_problems_come_back_as_error_results():
    assert call("no_such_tool", {})[0].is_error
    assert call("get_details", {})[0].is_error                      # invalid input
    out, _ = call("get_details", {"ref": "Dune"})                    # not a ref
    assert out.is_error and "isn't a ref" in out.text


def test_unexpected_failures_dont_escape():
    ctx = context()

    async def boom(*args, **kwargs):
        raise RuntimeError("TMDB exploded")

    ctx.tmdb.get_credits_and_keywords = boom
    out, _ = call("get_details", {"ref": "movie:438631"}, ctx)
    assert out.is_error and "failed" in out.text


# --- Tools

def test_search_returns_refs_and_caches_what_it_finds():
    out, ctx = call("search_titles", {"query": "Dune", "media_types": ["movie", "book", "paper"], "year": None})
    assert "movie:438631 · Dune · 2021" in out.text
    assert "book:OL893414W · Dune · 1965 · book · by Frank Herbert" in out.text
    assert "paper:p1 · Attention Is All You Need · 2017" in out.text
    assert {"movie:438631", "book:OL893414W", "paper:p1"} <= set(ctx.seen)
    assert "OL893414W" in ctx.supabase.books and "p1" in ctx.supabase.papers  # cards can be shown by ref
    assert ctx.tmdb.search_limits == [6]


def test_a_paper_outage_doesnt_sink_the_whole_search():
    out, _ = call("search_titles", {"query": "Dune", "media_types": ["movie", "paper"], "year": None}, context(s2_down=True))
    assert not out.is_error and "movie:438631" in out.text and "paper search is unavailable" in out.text


def test_movie_details_name_the_writers_and_the_source_book():
    out, ctx = call("get_details", {"ref": "movie:438631"})
    assert "Directed by: Denis Villeneuve (person:137427, Director)" in out.text
    assert "Frank Herbert (person:12506, Novel)" in out.text
    assert "Based on a book: yes" in out.text
    assert "person:12506" in ctx.seen


def test_book_details_load_and_save_the_description():
    ctx = context()
    ctx.supabase.books["OL893414W"] = Book(id="OL893414W", title="Dune", authors=[Author(name="Frank Herbert")])
    out, _ = call("get_details", {"ref": "book:OL893414W"}, ctx)
    assert "Set on the desert planet Arrakis." in out.text
    assert ctx.supabase.descriptions == {"OL893414W": "Set on the desert planet Arrakis."}


def test_unknown_book_asks_for_a_search_first():
    out, _ = call("get_details", {"ref": "book:OL1W"})
    assert out.is_error and "search_titles" in out.text


def test_person_lists_writing_credits_most_notable_first_without_duplicates():
    out, ctx = call("get_person", {"name": "Frank Herbert", "ref": None})
    writing = out.text.split("Writing (most notable first):\n")[1].splitlines()
    assert [line.split(" · ")[0] for line in writing] == ["movie:438631", "movie:841", "show:90228"]
    assert {"person:12506", "show:90228"} <= set(ctx.seen)


def test_person_by_ref_and_wrong_kind_of_ref():
    assert "Frank Herbert" in call("get_person", {"name": None, "ref": "person:12506"})[0].text
    out, _ = call("get_person", {"name": None, "ref": "movie:603"})
    assert out.is_error


def test_similar_titles_and_books_defer_to_claude():
    assert "movie:841" in call("find_similar", {"ref": "movie:438631"})[0].text
    out, _ = call("find_similar", {"ref": "book:OL893414W"})
    assert not out.is_error and "your own knowledge" in out.text


def test_recall_lists_what_was_shown_in_order():
    ctx = context()
    assert "nothing shown" in call("recall", {}, ctx)[0].text
    ctx.shown.update({"movie:438631": "Dune (2021)", "book:OL893414W": "Dune by Frank Herbert"})
    assert call("recall", {}, ctx)[0].text.splitlines() == [
        "1. movie:438631 · Dune (2021)", "2. book:OL893414W · Dune by Frank Herbert"]


def test_status_lines():
    ctx = context()
    ctx.seen["movie:438631"] = "Dune (2021)"
    assert tool_status("search_titles", {"query": "Dune", "media_types": ["movie"], "year": None}, ctx) == "Searching movies for “Dune”"
    assert tool_status("get_details", {"ref": "movie:438631"}, ctx) == "Looking up Dune (2021)"
    assert tool_status("get_details", {"bad": 1}, ctx) == "Working…"


# --- Hydrator

def hydrate(ref: str, supabase: FakeSupabase, tmdb: Optional[FakeTMDB] = None, s2: Optional[FakeS2] = None):
    tmdb = tmdb or FakeTMDB()

    async def run():
        item = await Hydrator(supabase, tmdb, s2 or FakeS2()).hydrate(MediaRef.parse(ref))
        await asyncio.sleep(0)
        return item

    return asyncio.run(run()), tmdb


def test_fresh_cached_movie_needs_no_tmdb_call():
    supabase = FakeSupabase()
    supabase.content[603] = {"id": 603, "title": "The Matrix", "fresh": True}
    item, tmdb = hydrate("movie:603", supabase)
    assert isinstance(item, Movie) and item.title == "The Matrix" and tmdb.details_fetched == []


def test_missing_or_stale_movie_is_fetched_and_cached():
    supabase = FakeSupabase()
    item, tmdb = hydrate("movie:603", supabase)
    assert item.title == "Fresh from TMDB" and tmdb.details_fetched == [603] and supabase.saved_content == [603]


def test_books_and_papers_hydrate_from_the_cache_then_semantic_scholar():
    supabase = FakeSupabase()
    supabase.books["OL1W"] = Book(id="OL1W", title="Cached Book")
    assert hydrate("book:OL1W", supabase)[0].title == "Cached Book"
    assert hydrate("book:OL2W", supabase)[0] is None
    paper, _ = hydrate("paper:fetch-me", supabase)
    assert paper.title == "Fetched Paper" and "fetch-me" in supabase.papers


def test_people_and_failures_hydrate_to_nothing():
    assert hydrate("person:12506", FakeSupabase())[0] is None
    supabase = FakeSupabase()

    async def boom(*args):
        raise RuntimeError("db down")

    supabase.get_content_by_id = boom
    assert hydrate("movie:603", supabase)[0] is None
