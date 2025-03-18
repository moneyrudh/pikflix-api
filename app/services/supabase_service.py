from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import json
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions
from app.config import SUPABASE_URL, SUPABASE_KEY, CACHE_DURATION
from datetime import datetime, timedelta, timezone, date

class SupabaseService:
    def __init__(self):
        self.schema = "pikflix"
        self.client: Client = create_client(
            SUPABASE_URL, 
            SUPABASE_KEY, 
            options=ClientOptions().replace(schema=self.schema)
        )
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
        cache_expiry = datetime.now(timezone.utc) - timedelta(hours=CACHE_DURATION)
        
        for movie in movie_recommendations:
            # Construct a query that searches for movies with matching title
            # and optionally matching year if provided
            query = self.client.table(self.table).select("*").ilike("title", f"%{movie['title']}%")
            
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
                    
                    # No need to manually parse JSON fields anymore
                    # Supabase client will return proper Python objects for JSONB fields
                    
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
            try:
                # Set the last_updated timestamp
                movie['last_updated'] = datetime.now().isoformat()
                
                # Convert any nested objects to JSON strings
                prepared_movie = self._prepare_for_db(movie)
                
                # Verify all complex objects are strings before sending to Supabase
                # for field in ['genres', 'production_companies', 'production_countries', 
                #             'spoken_languages', 'belongs_to_collection']:
                #     if field in prepared_movie and prepared_movie[field] is not None:
                #         if not isinstance(prepared_movie[field], str):
                #             print(f"WARNING: {field} is still not a string after preparation!")
                #             prepared_movie[field] = json.dumps(prepared_movie[field])
                
                print(f"Saving movie: {prepared_movie.get('title')}")
                
                # Upsert the movie (insert if not exists, update if exists)
                self.client.table(self.table).upsert(prepared_movie).execute()
            except Exception as e:
                print(f"Error saving movie {movie.get('title', 'unknown')}: {str(e)}")
                print(f"Error details: {e.__class__.__name__}")
                # Just in case there's some field causing issues, let's log the keys
                print(f"Movie fields: {list(movie.keys())}")  
    
    def _prepare_for_db(self, movie: Dict[str, Any]) -> Dict[str, Any]:
        """
        Prepare a movie object for database storage by converting complex objects to JSON
        """
        # Create a copy to avoid modifying the original
        movie_copy = movie.copy()

        fields_to_remove = ['reason']  # add any other fields here
        for field in fields_to_remove:
            if field in movie_copy:
                del movie_copy[field]
        
        if 'release_date' in movie_copy and isinstance(movie_copy['release_date'], date):
            movie_copy['release_date'] = movie_copy['release_date'].isoformat()
        # # Convert complex nested objects to JSON strings
        # json_fields = ['genres', 'production_companies', 'production_countries', 
        #                'spoken_languages', 'belongs_to_collection']
        
        # for field in json_fields:
        #     if field in movie_copy and movie_copy[field] is not None:
        #         # Skip if it's already a string (this can happen when retrieving from DB)
        #         if not isinstance(movie_copy[field], str):
        #             print(f"Converting {field} to JSON string")
        #             movie_copy[field] = json.dumps(movie_copy[field])
        #         else:
        #             print(f"{field} is already a string")

        return movie_copy

    async def get_movie_providers(self, movie_id: int, region: str = None) -> Dict[str, Any]:
        """
        Get movie providers from Supabase
        
        Args:
            movie_id: The movie ID to get providers for
            region: Optional region code to filter results
            
        Returns:
            Dictionary with provider data or None if not found
        """
        query = self.client.table("providers").select("*").eq("movie_id", movie_id)
        result = query.execute()
        
        if not result.data or len(result.data) == 0:
            return None
        
        provider_data = result.data[0]
        
        # Return data for specific region if requested
        if region:
            column_name = f"country_{region}"
            if column_name in provider_data and provider_data[column_name]:
                return {
                    "id": movie_id,
                    "results": {
                        region: provider_data[column_name]
                    }
                }
            # Region not found in database
            return {
                "id": movie_id,
                "results": {
                    region: {}
                }
            }
        
        # Process all country fields and map them from "country_XX" to "XX" format
        countries_data = {}
        for k, v in provider_data.items():
            if k.startswith("country_") and v is not None:
                country_code = k[8:]  # Extract country code by removing "country_" prefix
                countries_data[country_code] = v
        
        return {
            "id": movie_id,
            "results": countries_data
        }

    async def save_movie_providers(self, movie_id: int, provider_data: Dict[str, Any]) -> None:
        """
        Save or update movie providers in the database
        
        Args:
            movie_id: The movie ID
            provider_data: Provider data from TMDB API
        """
        try:
            # Prepare data for insertion
            data = {
                "movie_id": movie_id,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            
            # Add country-specific provider data with "country_" prefix
            if "results" in provider_data:
                for country_code, country_data in provider_data["results"].items():
                    if country_data:  # Only add non-empty data
                        column_name = f"country_{country_code}"
                        data[column_name] = country_data
            
            # Check if record exists
            query = self.client.table("providers").select("movie_id").eq("movie_id", movie_id)
            result = query.execute()
            
            if result.data and len(result.data) > 0:
                # Update existing record
                self.client.table("providers").update(data).eq("movie_id", movie_id).execute()
            else:
                # Insert new record
                self.client.table("providers").insert(data).execute()
                
            print(f"Saved providers for movie ID: {movie_id}")
        except Exception as e:
            print(f"Error saving providers for movie ID {movie_id}: {str(e)}")
            print(f"Error details: {e.__class__.__name__}")