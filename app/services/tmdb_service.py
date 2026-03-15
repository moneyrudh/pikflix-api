import asyncio
import logging
from typing import List, Dict, Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class TMDBService:
    def __init__(self):
        self.access_token = settings.TMDB_READ_ACCESS_TOKEN
        self.base_url = settings.TMDB_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json;charset=utf-8"
        }
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Reuse a single httpx client to avoid per-request TCP handshakes."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0, headers=self.headers)
        return self._client

    async def search_movies(self, query: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/search/movie"
        params: Dict[str, Any] = {"query": query}
        if year:
            params["year"] = year

        client = await self._get_client()
        response = await client.get(url, params=params)

        if response.status_code == 200:
            results = response.json().get("results", [])
            return results[:1] if results else []
        return []

    async def get_movie_details(self, movie_id: int) -> Dict[str, Any]:
        url = f"{self.base_url}/movie/{movie_id}"
        client = await self._get_client()
        response = await client.get(url)

        if response.status_code == 200:
            return response.json()
        return {}

    async def _fetch_single_movie(self, movie: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Resolve a single recommendation to full TMDB details."""
        reason = movie.get("reason", "")
        movie_data = None

        if movie.get("id"):
            movie_data = await self.get_movie_details(movie["id"])
        else:
            search_results = await self.search_movies(movie["title"], movie.get("year"))
            if search_results:
                movie_data = await self.get_movie_details(search_results[0]["id"])

        if movie_data:
            movie_data["reason"] = reason
        return movie_data

    async def fetch_movie_data(self, movie_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Fetch detailed movie data for all movies in parallel.

        Previous implementation fetched sequentially (one network call at a time).
        Using asyncio.gather cuts wall-clock time from O(n) to O(1) for n movies.
        """
        tasks = [self._fetch_single_movie(m) for m in movie_list]
        settled = await asyncio.gather(*tasks, return_exceptions=True)

        results: List[Dict[str, Any]] = []
        for item, movie in zip(settled, movie_list):
            if isinstance(item, Exception):
                logger.error("Failed to fetch movie '%s': %s", movie.get("title"), item)
            elif item is not None:
                results.append(item)
        return results

    async def get_movie_providers(self, movie_id: int) -> Dict[str, Any]:
        url = f"{self.base_url}/movie/{movie_id}/watch/providers"
        client = await self._get_client()
        response = await client.get(url)

        if response.status_code == 200:
            return response.json()
        return {"id": movie_id, "results": {}}