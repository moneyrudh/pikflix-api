import json
from typing import List
import anthropic
from app.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL


class AnthropicService:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = ANTHROPIC_MODEL

    async def get_movie_recommendations(self, query: str) -> List[dict]:
        """
        Get movie recommendations based on user query using Anthropic API
        Returns a list of movie titles and years suitable for TMDB lookup
        """
        system_prompt = """
        You are a movie recommendation assistant. Given a user's requirements, 
        provide exactly 9 diverse movie recommendations that match their criteria.
        
        Return your response as a JSON array of objects with the following fields:
        - title: The movie title
        - year: The release year (if known)
        - reason: A brief explanation of why this movie matches the query (max 1 sentence)
        
        Do not include any other text in your response, just the JSON array.
        Make sure the results are diverse in terms of era, style, and popularity.
        """

        user_message = f"Find me movies that match this description: {query}"

        try:
            response = self.client.messages.create(
                model=self.model,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=1024,
            )
            
            # Extract the content from the response
            content = response.content[0].text
            
            # Parse the JSON content
            movies = json.loads(content)
            
            # Ensure we have exactly 9 recommendations (or less if Anthropic returns fewer)
            return movies[:9]
            
        except Exception as e:
            print(f"Error calling Anthropic API: {str(e)}")
            # Return an empty list in case of error
            return []