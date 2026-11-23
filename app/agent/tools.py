"""
The agent's tools. Each is one entry in TOOLS: a Pydantic input model (sent to Claude as a strict
schema), a status line for the UI while it runs, and a handler that returns compact text for Claude.

Tool results are short text, not raw API JSON: a TMDB payload is several KB and would fill the
context fast. Every item in a result carries a ref ("movie:603"), and every ref handed out is
recorded in ToolContext.seen; the agent's reply may only cite refs from that set.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models import Book, ContentType, MediaRef, MediaType, OpenLibraryDoc, Paper, RefType, S2Paper
from app.services.openlibrary_service import OpenLibraryService, lookup_key
from app.services.semantic_scholar_service import SemanticScholarError, SemanticScholarService
from app.services.supabase_service import SupabaseService
from app.services.tmdb_service import TMDBService

logger = logging.getLogger(__name__)

RESULTS_PER_TYPE = 6
OVERVIEW_CHARS = 140
LONG_TEXT_CHARS = 600


@dataclass
class ToolContext:
    tmdb: TMDBService
    openlibrary: OpenLibraryService
    semantic_scholar: SemanticScholarService
    supabase: SupabaseService
    seen: Dict[str, str] = field(default_factory=dict)   # ref -> label, for every ref a tool returned this chat
    shown: Dict[str, str] = field(default_factory=dict)  # ref -> label, for refs already shown as cards

    def remember(self, ref: str, label: str) -> str:
        self.seen.setdefault(ref, label)
        return ref


class ToolOutput(BaseModel):
    text: str
    is_error: bool = False


class ToolError(Exception):
    """A problem Claude should see and can work around (bad ref, nothing found)."""


# --- Formatting helpers

def _year(date: Optional[str]) -> str:
    return (date or "")[:4] or "?"


def _short(text: Optional[str], limit: int = OVERVIEW_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _watch_line(ctx: ToolContext, content_type: ContentType, item: Dict[str, Any], extra: str = "") -> str:
    title = item.get("name" if content_type == ContentType.SHOW else "title") or "?"
    year = _year(item.get("first_air_date" if content_type == ContentType.SHOW else "release_date"))
    ref = ctx.remember(f"{content_type.value}:{item['id']}", f"{title} ({year})")
    return f"{ref} · {title} · {year} · {content_type.value}{' · ' + extra if extra else ''} · {_short(item.get('overview'))}"


def _book_line(ctx: ToolContext, book: Book) -> str:
    author = book.authors[0].name if book.authors else "unknown author"
    ref = ctx.remember(f"book:{book.id}", f"{book.title} by {author}")
    return f"{ref} · {book.title} · {book.first_publish_year or '?'} · book · by {author}"


def _paper_line(ctx: ToolContext, paper: Paper) -> str:
    ref = ctx.remember(f"paper:{paper.id}", paper.title)
    venue = paper.venue or "venue unknown"
    cites = f"{paper.citation_count} citations" if paper.citation_count is not None else "citations unknown"
    return f"{ref} · {paper.title} · {paper.year or '?'} · paper · {venue}, {cites}"


def _parse_ref(value: str) -> MediaRef:
    try:
        return MediaRef.parse(value)
    except ValueError:
        raise ToolError(f"'{value}' isn't a ref. Use a ref exactly as a tool returned it, e.g. movie:603.")


def _cache_books(ctx: ToolContext, docs: List[OpenLibraryDoc]) -> List[Book]:
    """Books a tool returns are cached, so their cards can be shown by ref."""
    books = [doc.to_book() for doc in docs]
    for doc, book in zip(docs, books):
        asyncio.create_task(ctx.supabase.save_book(book, lookup_key(doc.title, doc.author_name[0] if doc.author_name else "")))
    return books


def _cache_papers(ctx: ToolContext, found: List[S2Paper]) -> List[Paper]:
    papers = [p.to_paper() for p in found]
    asyncio.create_task(ctx.supabase.save_papers(papers))
    return papers


# --- search_titles

class SearchTitles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(description="A title or name to look up (movie/show search matches titles; books and papers also match topics)")
    media_types: List[MediaType] = Field(description="Which kinds to search")
    year: Optional[int] = Field(description="Release or publication year, if known; null otherwise")


async def _search_type(ctx: ToolContext, args: SearchTitles, media_type: MediaType) -> List[str]:
    if media_type in (MediaType.MOVIE, MediaType.SHOW):
        content_type = ContentType(media_type.value)
        search = ctx.tmdb.search_shows if content_type == ContentType.SHOW else ctx.tmdb.search_movies
        return [_watch_line(ctx, content_type, r) for r in await search(args.query, args.year, RESULTS_PER_TYPE)]
    if media_type == MediaType.BOOK:
        books = _cache_books(ctx, await ctx.openlibrary.search_any(args.query, RESULTS_PER_TYPE))
        return [_book_line(ctx, b) for b in books]
    try:
        found = await ctx.semantic_scholar.search(args.query, RESULTS_PER_TYPE)
    except SemanticScholarError:
        return ["(paper search is unavailable right now)"]
    return [_paper_line(ctx, p) for p in _cache_papers(ctx, found)]


async def search_titles(args: SearchTitles, ctx: ToolContext) -> str:
    types = list(dict.fromkeys(args.media_types)) or [MediaType.MOVIE]
    sections = await asyncio.gather(*(_search_type(ctx, args, t) for t in types))
    out = [f"{t.value}s:\n" + ("\n".join(lines) if lines else "(no matches)") for t, lines in zip(types, sections)]
    return "\n\n".join(out)


# --- get_details

class GetDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(description="A ref exactly as a tool returned it, e.g. movie:603")


def _people(ctx: ToolContext, crew: List[Dict[str, Any]], match: Callable[[Dict[str, Any]], bool], limit: int = 6) -> str:
    names: Dict[int, str] = {}
    for person in crew:
        if match(person) and person["id"] not in names:
            job = person.get("job")
            ref = ctx.remember(f"person:{person['id']}", person["name"])
            names[person["id"]] = f"{person['name']} ({ref}{', ' + job if job else ''})"
    return "; ".join(list(names.values())[:limit]) or "unknown"


async def _watch_details(ctx: ToolContext, ref: MediaRef) -> str:
    content_type = ContentType(ref.type.value)
    data = await ctx.tmdb.get_credits_and_keywords(int(ref.id), content_type)
    if not data:
        raise ToolError(f"No details found for {ref}.")
    show = content_type == ContentType.SHOW
    title = data.get("name" if show else "title")
    year = _year(data.get("first_air_date" if show else "release_date"))
    ctx.remember(str(ref), f"{title} ({year})")
    credits = data.get("credits") or {}
    crew = credits.get("crew") or []
    keywords = [k["name"] for k in ((data.get("keywords") or {}).get("keywords" if not show else "results") or [])]
    based_on_book = any("based on" in k and ("novel" in k or "book" in k) for k in keywords)
    source_authors = _people(ctx, crew, lambda p: p.get("job") in ("Novel", "Book", "Author", "Characters"))
    lines = [
        f"{ref} · {title} ({year}) · {', '.join(g['name'] for g in data.get('genres') or []) or 'genre unknown'}",
        f"Overview: {_short(data.get('overview'), LONG_TEXT_CHARS)}",
        f"Seasons: {data.get('number_of_seasons')}" if show else f"Runtime: {data.get('runtime') or '?'} min",
        ("Created by: " + _people(ctx, data.get("created_by") or [], lambda p: True)) if show
        else ("Directed by: " + _people(ctx, crew, lambda p: p.get("job") == "Director")),
        "Writers: " + _people(ctx, crew, lambda p: p.get("department") == "Writing"),
        f"Based on a book: {'yes' if based_on_book or source_authors != 'unknown' else 'not that TMDB knows of'}"
        + (f" (source author: {source_authors})" if source_authors != "unknown" else ""),
        "Cast: " + "; ".join(c["name"] for c in (credits.get("cast") or [])[:5]),
    ]
    if keywords:
        lines.append("Keywords: " + ", ".join(keywords[:10]))
    return "\n".join(lines)


async def _book_details(ctx: ToolContext, ref: MediaRef) -> str:
    book = await ctx.supabase.get_book(ref.id)
    if book is None:
        raise ToolError(f"{ref} isn't known yet; find it with search_titles first.")
    description = book.description
    if book.description_checked_at is None:
        try:
            description = await ctx.openlibrary.get_description(book.id)
            asyncio.create_task(ctx.supabase.save_book_description(book.id, description))
        except Exception:
            logger.warning("Couldn't load description for %s", ref)
    ctx.remember(str(ref), book.title)
    return "\n".join([
        f"{ref} · {book.title}" + (f": {book.subtitle}" if book.subtitle else ""),
        "By: " + (", ".join(a.name for a in book.authors) or "unknown"),
        f"First published: {book.first_publish_year or '?'} · {book.page_count or '?'} pages · {book.edition_count or '?'} editions",
        "Subjects: " + (", ".join(book.subjects[:8]) or "none listed"),
        f"Description: {_short(description, LONG_TEXT_CHARS) or 'none available'}",
    ])


async def _load_paper(ctx: ToolContext, paper_id: str) -> Optional[Paper]:
    paper = await ctx.supabase.get_paper(paper_id)
    if paper is not None:
        return paper
    try:
        found = await ctx.semantic_scholar.get_paper(paper_id)
    except SemanticScholarError:
        return None
    return _cache_papers(ctx, [found])[0] if found else None


async def _paper_details(ctx: ToolContext, ref: MediaRef) -> str:
    paper = await _load_paper(ctx, ref.id)
    if paper is None:
        raise ToolError(f"No details found for {ref}.")
    ctx.remember(str(ref), paper.title)
    authors = ", ".join(a.name for a in paper.authors[:5]) + (" et al." if len(paper.authors) > 5 else "")
    return "\n".join([
        f"{ref} · {paper.title}",
        f"By: {authors or 'unknown'}",
        f"{paper.venue or 'venue unknown'} · {paper.year or '?'} · {paper.citation_count if paper.citation_count is not None else '?'} citations",
        f"TL;DR: {paper.tldr or 'none'}",
        f"Abstract: {_short(paper.abstract, LONG_TEXT_CHARS) or 'none available'}",
    ])


async def get_details(args: GetDetails, ctx: ToolContext) -> str:
    ref = _parse_ref(args.ref)
    if ref.type in (RefType.MOVIE, RefType.SHOW):
        return await _watch_details(ctx, ref)
    if ref.type == RefType.BOOK:
        return await _book_details(ctx, ref)
    if ref.type == RefType.PAPER:
        return await _paper_details(ctx, ref)
    return await get_person(GetPerson(name=None, ref=str(ref)), ctx)


# --- get_person

class GetPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(description="The person's name; null if you have their ref")
    ref: Optional[str] = Field(description="A person ref from a tool result, e.g. person:5632; null to search by name")


def _credit_lines(ctx: ToolContext, credits: List[Dict[str, Any]], limit: int) -> List[str]:
    seen: set[str] = set()
    lines = []
    for c in sorted(credits, key=lambda c: c.get("vote_count") or 0, reverse=True):
        kind = c.get("media_type")
        if kind not in ("movie", "tv"):
            continue
        content_type = ContentType.SHOW if kind == "tv" else ContentType.MOVIE
        key = f"{content_type.value}:{c['id']}"
        if key in seen:
            continue
        seen.add(key)
        role = c.get("job") or c.get("character") or ""
        lines.append(_watch_line(ctx, content_type, c, extra=role) if role else _watch_line(ctx, content_type, c))
        if len(lines) == limit:
            break
    return lines


async def get_person(args: GetPerson, ctx: ToolContext) -> str:
    if args.ref:
        ref = _parse_ref(args.ref)
        if ref.type != RefType.PERSON:
            raise ToolError(f"{ref} isn't a person; use get_details for titles.")
        person_id = int(ref.id)
    elif args.name:
        match = await ctx.tmdb.search_person(args.name)
        if match is None:
            raise ToolError(f"No person found named '{args.name}'.")
        person_id = match["id"]
    else:
        raise ToolError("Give a name or a person ref.")
    person = await ctx.tmdb.get_person(person_id)
    if not person:
        raise ToolError(f"No details found for person:{person_id}.")
    ref_text = ctx.remember(f"person:{person_id}", person["name"])
    credits = person.get("combined_credits") or {}
    crew = credits.get("crew") or []
    sections = [
        ("Writing", [c for c in crew if c.get("department") == "Writing"], 8),
        ("Directing", [c for c in crew if c.get("job") == "Director"], 6),
        ("Acting", credits.get("cast") or [], 6),
    ]
    out = [
        f"{ref_text} · {person['name']} · known for {person.get('known_for_department') or '?'}"
        + (f" · born {person['birthday']}" if person.get("birthday") else "")
        + (f", died {person['deathday']}" if person.get("deathday") else ""),
        f"Biography: {_short(person.get('biography'), LONG_TEXT_CHARS) or 'none available'}",
    ]
    for label, items, limit in sections:
        lines = _credit_lines(ctx, items, limit)
        if lines:
            out.append(f"{label} (most notable first):\n" + "\n".join(lines))
    return "\n".join(out)


# --- find_similar

class FindSimilar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(description="A ref exactly as a tool returned it")


async def find_similar(args: FindSimilar, ctx: ToolContext) -> str:
    ref = _parse_ref(args.ref)
    if ref.type in (RefType.MOVIE, RefType.SHOW):
        content_type = ContentType(ref.type.value)
        results = await ctx.tmdb.get_recommendations(int(ref.id), content_type)
        lines = [_watch_line(ctx, content_type, r) for r in results]
    elif ref.type == RefType.BOOK:
        # Open Library has no recommendations, and its subject search ranks by popularity, not
        # likeness (it returned The Art of War as similar to Dune). Claude knows similar books.
        return ("Open Library has no book recommendations. Suggest similar books from your own knowledge, "
                "then look each one up with search_titles to get its ref.")
    elif ref.type == RefType.PAPER:
        paper = await _load_paper(ctx, ref.id)
        if paper is None:
            raise ToolError(f"No details found for {ref}.")
        try:
            found = [p for p in await ctx.semantic_scholar.search(paper.title, RESULTS_PER_TYPE + 1) if p.paperId != ref.id]
        except SemanticScholarError:
            raise ToolError("Paper search is unavailable right now.")
        lines = [_paper_line(ctx, p) for p in _cache_papers(ctx, found[:RESULTS_PER_TYPE])]
    else:
        raise ToolError("find_similar works on titles, not people; use get_person for someone's work.")
    return "\n".join(lines) or "(nothing similar found)"


# --- recall

class Recall(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def recall(args: Recall, ctx: ToolContext) -> str:
    if not ctx.shown:
        return "(nothing shown yet in this chat)"
    return "\n".join(f"{i}. {ref} · {label}" for i, (ref, label) in enumerate(ctx.shown.items(), 1))


# --- Registry

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: Type[BaseModel]
    run: Callable[[Any, ToolContext], Awaitable[str]]
    status: Callable[[Any, ToolContext], str]  # shown in the UI while the tool runs


def _label(ctx: ToolContext, ref: str) -> str:
    return ctx.seen.get(ref, ref)


TOOLS: List[Tool] = [
    Tool("search_titles",
         "Find movies, shows, books or papers. Returns refs (e.g. movie:603) with title, year and a short "
         "summary. Movie and show search matches titles, so search for specific titles you have in mind; "
         "book and paper search also matches topics.",
         SearchTitles, search_titles,
         lambda a, c: f"Searching {', '.join(t.value + 's' for t in a.media_types)} for “{a.query}”"),
    Tool("get_details",
         "Key facts about one ref: for movies and shows the director or creators, writers, cast, and whether "
         "it is based on a book; for books the authors, subjects and description; for papers the abstract.",
         GetDetails, get_details,
         lambda a, c: f"Looking up {_label(c, a.ref)}"),
    Tool("get_person",
         "A person's short biography and their most notable work as writer, director and actor, with refs.",
         GetPerson, get_person,
         lambda a, c: f"Looking up {a.name or _label(c, a.ref or '')}"),
    Tool("find_similar",
         "Titles similar to a movie, show or paper ref (same kind), with refs. For books, suggest similar "
         "books yourself and look them up with search_titles.",
         FindSimilar, find_similar,
         lambda a, c: f"Finding titles like {_label(c, a.ref)}"),
    Tool("recall",
         "Items already shown in this chat, numbered in order, so references like 'the second one' resolve.",
         Recall, recall,
         lambda a, c: "Checking what we've covered"),
]

_BY_NAME = {tool.name: tool for tool in TOOLS}


def tool_definitions() -> List[Dict[str, Any]]:
    """The tools as Claude API definitions (strict, so inputs always match the schema)."""
    return [{
        "name": tool.name,
        "description": tool.description,
        "input_schema": anthropic.transform_schema(tool.input_model.model_json_schema()),
        "strict": True,
    } for tool in TOOLS]


def tool_status(name: str, raw_input: Dict[str, Any], ctx: ToolContext) -> str:
    tool = _BY_NAME.get(name)
    if tool is None:
        return "Working…"
    try:
        return tool.status(tool.input_model.model_validate(raw_input), ctx)
    except ValidationError:
        return "Working…"


async def run_tool(name: str, raw_input: Dict[str, Any], ctx: ToolContext) -> ToolOutput:
    """Run a tool call. Problems come back as is_error results Claude can adapt to, never as exceptions."""
    tool = _BY_NAME.get(name)
    if tool is None:
        return ToolOutput(text=f"There is no tool named {name}.", is_error=True)
    try:
        args = tool.input_model.model_validate(raw_input)
    except ValidationError as e:
        return ToolOutput(text=f"Invalid input for {name}: {e.errors()[0]['msg']}", is_error=True)
    try:
        return ToolOutput(text=await tool.run(args, ctx))
    except ToolError as e:
        return ToolOutput(text=str(e), is_error=True)
    except Exception:
        logger.exception("Tool %s failed", name)
        return ToolOutput(text=f"{name} failed; try another approach.", is_error=True)
