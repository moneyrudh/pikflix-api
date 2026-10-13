import logging
from datetime import datetime, timezone
from functools import cache
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)
from app.config import TMDB_READ_ACCESS_TOKEN, TMDB_BASE_URL
from app.models import ContentType, FetchRequest, TMDBVideo, Trailer

SearchFn = Callable[[str, Optional[int]], Awaitable[List[Dict[str, Any]]]]
DetailFn = Callable[[int], Awaitable[Dict[str, Any]]]


def pick_trailer(videos: List[TMDBVideo], language: str = "en") -> Optional[Trailer]:
    """
    Choose the video to show: YouTube trailers (teasers if there are none), ranked by
    official, then language match, then resolution, then earliest. Later uploads tend to be
    re-release and home-video promos, so the earliest is usually the original campaign trailer.
    """
    youtube = [v for v in videos if v.site == "YouTube"]
    candidates = [v for v in youtube if v.type == "Trailer"] or [v for v in youtube if v.type == "Teaser"]
    if not candidates:
        return None

    best = max(candidates, key=lambda v: (
        v.official,
        v.iso_639_1 == language,
        v.size or 0,
        -v.published_at.timestamp() if v.published_at else float("-inf"),
    ))
    return Trailer(key=best.key, name=best.name, official=best.official, language=best.iso_639_1)


class TMDBService:
    def __init__(self):
        self.access_token = TMDB_READ_ACCESS_TOKEN
        self.base_url = TMDB_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json;charset=utf-8"
        }
        # One pooled client for every TMDB call, instead of a new connection (and TLS handshake) per request
        self.client = httpx.AsyncClient(timeout=30.0, headers=self.headers)

    async def close(self) -> None:
        await self.client.aclose()
    
    async def search_movies(self, query: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Search for movies by title and optionally year
        """
        url = f"{self.base_url}/search/movie"
        params: Dict[str, Any] = {"query": query}
        
        if year:
            params["year"] = year
        
        response = await self.client.get(url, params=params)
        
        if response.status_code == 200:
            results = response.json().get("results", [])
            return results[:1] if results else []  # Return only the top match
        
        return []

    async def get_movie_details(self, movie_id: int) -> Dict[str, Any]:
        """
        Get detailed information about a movie by its ID, including its trailer
        """
        return await self._get_details(f"{self.base_url}/movie/{movie_id}")

    async def _get_details(self, url: str) -> Dict[str, Any]:
        """
        Fetch a movie/show details payload with its videos in the same request, and replace
        the raw `videos` list with the chosen `trailer` so the dict matches the DB columns.
        """
        response = await self.client.get(url, params={"append_to_response": "videos"})
        if response.status_code != 200:
            return {}

        data: Dict[str, Any] = response.json()
        videos = [TMDBVideo.model_validate(v) for v in data.pop("videos", {}).get("results", [])]
        trailer = pick_trailer(videos)
        data["trailer"] = trailer.model_dump() if trailer else None
        data["videos_checked_at"] = datetime.now(timezone.utc).isoformat()
        return data

    async def fetch_movie_data(self, movie_list: List[FetchRequest]) -> List[Dict[str, Any]]:
        """
        Fetch detailed movie data for each movie in the list

        If a movie has an ID, fetch directly. Otherwise, search by title/year.
        """
        results = []

        for movie in movie_list:
            logger.info("Fetching movie from TMDB: %s", movie.title)
            movie_data = None
            reason = movie.reason or ''

            if movie.id:
                # Direct fetch by ID
                movie_data = await self.get_movie_details(movie.id)
            else:
                # Search by title and year
                search_results = await self.search_movies(movie.title, movie.year)
                if search_results:
                    movie_id = search_results[0]['id']
                    movie_data = await self.get_movie_details(movie_id)

            if movie_data:
                movie_data['reason'] = reason
                results.append(movie_data)

        return results
    
    async def get_movie_providers(self, movie_id: int) -> Dict[str, Any]:
        """
        Get movie watch providers (streaming services) from TMDB API
        Returns data for all available regions
        """
        url = f"{self.base_url}/movie/{movie_id}/watch/providers"

        response = await self.client.get(url)

        if response.status_code == 200:
            return response.json()

        # Return empty structure if API call fails
        return {"id": movie_id, "results": {}}

    async def search_shows(self, query: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/search/tv"
        params: Dict[str, Any] = {"query": query}
        if year:
            params["first_air_date_year"] = year

        response = await self.client.get(url, params=params)
        if response.status_code == 200:
            results = response.json().get("results", [])
            return results[:1] if results else []
        return []

    async def get_show_details(self, show_id: int) -> Dict[str, Any]:
        return await self._get_details(f"{self.base_url}/tv/{show_id}")

    async def fetch_show_data(self, show_list: List[FetchRequest]) -> List[Dict[str, Any]]:
        results = []
        for show in show_list:
            logger.info("Fetching show from TMDB: %s", show.title)
            show_data = None
            reason = show.reason or ''

            if show.id:
                show_data = await self.get_show_details(show.id)
            else:
                search_results = await self.search_shows(show.title, show.year)
                if search_results:
                    show_id = search_results[0]['id']
                    show_data = await self.get_show_details(show_id)

            if show_data:
                show_data['reason'] = reason
                results.append(show_data)
        return results

    async def get_show_providers(self, show_id: int) -> Dict[str, Any]:
        url = f"{self.base_url}/tv/{show_id}/watch/providers"
        response = await self.client.get(url)
        if response.status_code == 200:
            return response.json()
        return {"id": show_id, "results": {}}

    async def fetch_content_data(self, content_list: List[FetchRequest], content_type: ContentType) -> List[Dict[str, Any]]:
        if content_type == ContentType.SHOW:
            return await self.fetch_show_data(content_list)
        return await self.fetch_movie_data(content_list)

    async def get_content_providers(self, content_id: int, content_type: ContentType) -> Dict[str, Any]:
        if content_type == ContentType.SHOW:
            return await self.get_show_providers(content_id)
        return await self.get_movie_providers(content_id)

    async def search_content(self, title: str, year: Optional[int], content_type: ContentType) -> tuple[Optional[Dict[str, Any]], ContentType]:
        """
        Search TMDB for content. Tries the given content_type first.
        If no result, falls back to the other type.
        Returns (detail_data, resolved_content_type) or (None, original_type).
        """
        lookups: Dict[ContentType, Tuple[SearchFn, DetailFn]] = {
            ContentType.MOVIE: (self.search_movies, self.get_movie_details),
            ContentType.SHOW: (self.search_shows, self.get_show_details),
        }
        fallback_type = ContentType.SHOW if content_type == ContentType.MOVIE else ContentType.MOVIE

        # Try the classified type first, then fall back to the other type
        for attempt_type in (content_type, fallback_type):
            if attempt_type == fallback_type:
                logger.info("Fallback search for '%s' as %s", title, fallback_type.value)
            search, detail = lookups[attempt_type]
            results = await search(title, year)
            if results:
                data = await detail(results[0]['id'])
                if data:
                    return data, attempt_type

        return None, content_type


@cache
def get_tmdb_service() -> TMDBService:
    return TMDBService()
