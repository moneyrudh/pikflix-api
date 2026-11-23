import asyncio
import logging
from functools import cache
from typing import Optional, Union

from app.models import Book, ContentType, MediaRef, Movie, Paper, RefType, Show
from app.services.semantic_scholar_service import SemanticScholarError, SemanticScholarService, get_semantic_scholar_service
from app.services.supabase_service import SupabaseService, get_supabase_service
from app.services.tmdb_service import TMDBService, get_tmdb_service

logger = logging.getLogger(__name__)

Item = Union[Movie, Show, Book, Paper]


class Hydrator:
    """Turns a ref into its full item, cache first. Used for the agent's blocks and to re-render
    reopened chats. Returns None when the ref can't be resolved (or is a person, which isn't a card)."""

    def __init__(self, supabase: SupabaseService, tmdb: TMDBService, semantic_scholar: SemanticScholarService) -> None:
        self._supabase = supabase
        self._tmdb = tmdb
        self._s2 = semantic_scholar

    async def hydrate(self, ref: MediaRef) -> Optional[Item]:
        try:
            if ref.type in (RefType.MOVIE, RefType.SHOW):
                return await self._watch(ref)
            if ref.type == RefType.BOOK:
                # Books enter the cache when a search or tool finds them; any age is fine to display
                return await self._supabase.get_book(ref.id)
            if ref.type == RefType.PAPER:
                return await self._paper(ref.id)
        except Exception:
            logger.exception("Couldn't hydrate %s", ref)
        return None

    async def _watch(self, ref: MediaRef) -> Optional[Union[Movie, Show]]:
        content_type = ContentType(ref.type.value)
        content_id = int(ref.id)
        row = await self._supabase.get_content_by_id(content_id, content_type)
        if row is None or not self._supabase.is_fresh_content(row):
            details = await self._tmdb.get_details(content_id, content_type)
            if not details:
                return None
            asyncio.create_task(self._supabase.save_content([details], content_type))
            row = details
        model = Show if content_type == ContentType.SHOW else Movie
        return model.model_validate(row)

    async def _paper(self, paper_id: str) -> Optional[Paper]:
        cached = await self._supabase.get_paper(paper_id)
        if cached is not None:
            return cached
        try:
            found = await self._s2.get_paper(paper_id)
        except SemanticScholarError as e:
            logger.warning("Couldn't fetch paper %s: %s", paper_id, e)
            return None
        if found is None:
            return None
        paper = found.to_paper()
        asyncio.create_task(self._supabase.save_papers([paper]))
        return paper


@cache
def get_hydrator() -> Hydrator:
    return Hydrator(get_supabase_service(), get_tmdb_service(), get_semantic_scholar_service())
