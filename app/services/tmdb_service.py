import logging
from typing import List, Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)
from app.config import TMDB_READ_ACCESS_TOKEN, TMDB_BASE_URL
from app.models import ContentType, FetchRequest


class TMDBService:
    def __init__(self):
        self.access_token = TMDB_READ_ACCESS_TOKEN
        self.base_url = TMDB_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json;charset=utf-8"
        }
    
    async def search_movies(self, query: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Search for movies by title and optionally year
        """
        url = f"{self.base_url}/search/movie"
        params = {"query": query}
        
        if year:
            params["year"] = year
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url, 
                headers=self.headers, 
                params=params
            )
            
            if response.status_code == 200:
                results = response.json().get("results", [])
                return results[:1] if results else []  # Return only the top match
            
            return []
    
    async def get_movie_details(self, movie_id: int) -> Dict[str, Any]:
        """
        Get detailed information about a movie by its ID
        """
        url = f"{self.base_url}/movie/{movie_id}"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url, 
                headers=self.headers
            )
            
            if response.status_code == 200:
                return response.json()
            
            return {}
    
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                url,
                headers=self.headers
            )

            if response.status_code == 200:
                return response.json()

            # Return empty structure if API call fails
            return {"id": movie_id, "results": {}}

    async def search_shows(self, query: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/search/tv"
        params = {"query": query}
        if year:
            params["first_air_date_year"] = year

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                results = response.json().get("results", [])
                return results[:1] if results else []
            return []

    async def get_show_details(self, show_id: int) -> Dict[str, Any]:
        url = f"{self.base_url}/tv/{show_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return {}

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
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=self.headers)
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
        # Try primary type first
        if content_type == ContentType.MOVIE:
            primary_search, fallback_search = self.search_movies, self.search_shows
            primary_detail, fallback_detail = self.get_movie_details, self.get_show_details
            fallback_type = ContentType.SHOW
        else:
            primary_search, fallback_search = self.search_shows, self.search_movies
            primary_detail, fallback_detail = self.get_show_details, self.get_movie_details
            fallback_type = ContentType.MOVIE

        results = await primary_search(title, year)
        if results:
            data = await primary_detail(results[0]['id'])
            if data:
                return data, content_type

        # Fallback to the other type
        logger.info("Fallback search for '%s' as %s", title, fallback_type.value)
        results = await fallback_search(title, year)
        if results:
            data = await fallback_detail(results[0]['id'])
            if data:
                return data, fallback_type

        return None, content_type