from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import json
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_KEY, CACHE_DURATION


class SupabaseService:
    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.schema = "pikflix"
        self.table = "movies"
        self.full_table = f"{self.schema}.{self.table}"
    
    async def get_movies_by_titles(self, movie_recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Check if movies exist in the database and determine which need to be fetched/refreshed
        
        Returns:
            Dict with:
            - 'found_movies': List of movies found in DB that don't need refresh
            - 'to_fetch': List of movies that need to be fetched from TMDB
        """
        found_movies = []
        to_fetch = []
        
        # Calculate the cache expiry timestamp
        cache_expiry = datetime.now() - timedelta(hours=CACHE_DURATION)
        
        for movie in movie_recommendations:
            # Construct a query that searches for movies with matching title
            # and optionally matching year if provided
            query = self.client.from_(self.full_table).select("*").ilike("title", f"%{movie['title']}%")
            
            if 'year' in movie and movie['year']:
                # If year is provided, add it to the query
                query = query.filter("release_date", "gte", f"{movie['year']}-01-01")
                query = query.filter("release_date", "lte", f"{movie['year']}-12-31")
            
            result = query.execute()
            
            if result.data and len(result.data) > 0:
                # Check if the movie needs to be refreshed
                movie_data = result.data[0]
                last_updated = datetime.fromisoformat(movie_data['last_updated'])
                
                if last_updated < cache_expiry:
                    # Movie is in DB but cache is stale
                    to_fetch.append({
                        "title": movie['title'], 
                        "year": movie.get('year'),
                        "reason": movie.get('reason'),
                        "id": movie_data['id']  # Include the id for direct TMDB fetch
                    })
                else:
                    # Movie is in DB and cache is fresh
                    movie_data['reason'] = movie.get('reason', '')
                    found_movies.append(movie_data)
            else:
                # Movie not in DB, need to fetch
                to_fetch.append({
                    "title": movie['title'], 
                    "year": movie.get('year'),
                    "reason": movie.get('reason')
                })
        
        return {
            "found_movies": found_movies,
            "to_fetch": to_fetch
        }
    
    async def save_movies(self, movies: List[Dict[str, Any]]) -> None:
        """
        Save or update movies in the database
        """
        for movie in movies:
            # Set the last_updated timestamp
            movie['last_updated'] = datetime.now().isoformat()
            
            # Convert any nested objects to JSON strings
            movie = self._prepare_for_db(movie)
            
            # Upsert the movie (insert if not exists, update if exists)
            self.client.from_(self.full_table).upsert(movie).execute()
    
    def _prepare_for_db(self, movie: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare a movie object for database storage by converting complex objects to JSON
        """
        # Create a copy to avoid modifying the original
        movie_copy = movie.copy()
        
        # Convert complex nested objects to JSON strings
        json_fields = ['genres', 'production_companies', 'production_countries', 
                       'spoken_languages', 'belongs_to_collection']
        
        for field in json_fields:
            if field in movie_copy and movie_copy[field] is not None:
                # Skip if it's already a string (this can happen when retrieving from DB)
                if not isinstance(movie_copy[field], str):
                    movie_copy[field] = json.dumps(movie_copy[field])
        
        return movie_copy