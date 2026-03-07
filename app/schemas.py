from typing import List
from pydantic import BaseModel, Field


class MovieRecommendation(BaseModel):
    title: str = Field(description="The movie title")
    year: int = Field(description="The release year")
    reason: str = Field(description="A brief one-sentence explanation of why this movie matches the query")


class MovieRecommendations(BaseModel):
    movies: List[MovieRecommendation] = Field(description="A list of exactly 9 movie recommendations")
