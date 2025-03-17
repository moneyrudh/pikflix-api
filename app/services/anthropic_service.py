import json
from typing import List
import anthropic
import httpx
from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL


class AnthropicService:
    def __init__(self):
        # Create a custom httpx client without problematic options
        http_client = httpx.Client(http2=True)
        
        # Initialize with custom client to avoid socket_options issue
        self.client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            http_client=http_client
        )
        self.model = ANTHROPIC_MODEL

    async def get_movie_recommendations(self, query: str) -> List[dict]:
        """
        Get movie recommendations based on user query using Anthropic API
        Returns a list of movie titles and years suitable for TMDB lookup
        """
        system_prompt = """
        You are a movie recommendation assistant. Given a user's requirements, 
        provide exactly 9 diverse movie recommendations that match their criteria.
        
        For each recommendation, return a JSON object with the following fields:
        - title: The movie title
        - year: The release year (if known)
        - reason: A brief explanation of why this movie matches the query (max 1 sentence)
        
        After each JSON object, including the last one, append this delimiter that's within the quotes: "\\u001E"
        
        Do not include any other text in your response, just the JSON array.
        Make sure the results are diverse in terms of era, style, and popularity.
        If the user searched for a specific movie or a prompt that warrants less than 9 movie responses, 
        smartly recommend the remaining movies that are the closest match with the movie the user asked for.
        However keep in mind that you MUST ONLY give exactly 9 movies. No more. No less.
        The user may only require one movie, but you can recommend similar movies based on title, director, genre, etc.
        """

        buffer = ""
        delimiter = "\\u001E"
        user_message = f"Find me movies that match this description: {query}"

        try:
            with self.client.messages.stream(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=1024,
            ) as stream:
                for text in stream.text_stream:
                    buffer += text
                    
                    # Check if we have a complete movie object with delimiter
                    if delimiter in buffer:
                        parts = buffer.split(delimiter)
                        # Process all complete parts except possibly the last one
                        for part in parts[:-1]:
                            if part.strip():
                                try:
                                    movie = json.loads(part)
                                    yield movie
                                except json.JSONDecodeError:
                                    print(f"Failed to parse JSON: {part}")
                        
                        # Keep the remainder (incomplete part) in the buffer
                        buffer = parts[-1]
        except Exception as e:
            print(f"Error streaming from Anthropic API: {str(e)}")
            return