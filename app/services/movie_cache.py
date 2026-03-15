import logging
from typing import Any, Dict, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# In-memory caches keyed by TMDB movie ID.
# Avoids a Supabase network round-trip for movies looked up recently.
# maxsize=500 ≈ ~55 concurrent recommendation sessions worth of data.
_movie_cache: TTLCache[int, Dict[str, Any]] = TTLCache(maxsize=500, ttl=3600)
_provider_cache: TTLCache[int, Dict[str, Any]] = TTLCache(maxsize=500, ttl=3600)


def get_movie(movie_id: int) -> Optional[Dict[str, Any]]:
    data = _movie_cache.get(movie_id)
    if data is not None:
        logger.debug("In-memory cache hit for movie %d", movie_id)
    return data


def put_movie(movie_data: Dict[str, Any]) -> None:
    movie_id = movie_data.get("id")
    if movie_id is not None:
        _movie_cache[movie_id] = movie_data


def get_provider(movie_id: int) -> Optional[Dict[str, Any]]:
    return _provider_cache.get(movie_id)


def put_provider(movie_id: int, provider_data: Dict[str, Any]) -> None:
    _provider_cache[movie_id] = provider_data
