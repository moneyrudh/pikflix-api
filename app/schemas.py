from typing import List
from pydantic import BaseModel, Field
from app.models import ContentType


class ContentRecommendation(BaseModel):
    title: str = Field(description="The title (movie title or show name)")
    year: int = Field(description="The release year or first air year")
    reason: str = Field(description="A brief one-sentence explanation of why this matches the query")
    content_type: ContentType = Field(description="Whether this is a movie or a show")


class ContentRecommendations(BaseModel):
    movies: List[ContentRecommendation] = Field(description="A list of exactly 9 recommendations")
