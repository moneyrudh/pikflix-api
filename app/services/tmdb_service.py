from typing import List, Dict, Any, Optional
import httpx
from app.config import TMDB_READ_ACCESS_TOKEN, TMDB_BASE_URL


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
    
    async def fetch_movie_data(self, movie_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Fetch detailed movie data for each movie in the list
        
        If a movie has an ID, fetch directly. Otherwise, search by title/year.
        """
        results = []
        
        for movie in movie_list:
            print("fetching movie in tmdb with title", movie['title'])
            movie_data = None
            reason = movie.get('reason', '')
            
            if 'id' in movie and movie['id']:
                # Direct fetch by ID
                movie_data = await self.get_movie_details(movie['id'])
            else:
                # Search by title and year
                title = movie['title']
                year = movie.get('year')
                
                search_results = await self.search_movies(title, year)
                if search_results:
                    movie_id = search_results[0]['id']
                    movie_data = await self.get_movie_details(movie_id)
            
            if movie_data:
                movie_data['reason'] = reason
                results.append(movie_data)
        
        return results