from typing import List, Optional
from pydantic import BaseModel, Field
from app.config import BOOK_PICKS_REQUESTED, PAPER_QUERIES_MAX, PAPER_RESULTS_SHOWN
from app.models import ContentType


class ContentRecommendation(BaseModel):
    title: str = Field(description="The title (movie title or show name)")
    year: int = Field(description="The release year or first air year")
    reason: str = Field(description="A brief one-sentence explanation of why this matches the query")
    content_type: ContentType = Field(description="Whether this is a movie or a show")


class ContentRecommendations(BaseModel):
    items: List[ContentRecommendation] = Field(description="A list of exactly 9 recommendations")


class BookPick(BaseModel):
    title: str = Field(description="The book's title as published, without series numbering")
    author: str = Field(description="The primary author's full name")
    year: Optional[int] = Field(description="Year of first publication, or null if unsure")
    reason: str = Field(description="A brief one-sentence explanation of why this matches the query")


class BookPicks(BaseModel):
    items: List[BookPick] = Field(description=f"A list of exactly {BOOK_PICKS_REQUESTED} recommendations")


class PaperQueries(BaseModel):
    """Step 1 of a paper search: keyword queries for Semantic Scholar."""
    queries: List[str] = Field(description=f"1 to {PAPER_QUERIES_MAX} short keyword search queries that together cover the request")


class PaperPick(BaseModel):
    paper_id: str = Field(description="The paper's id, copied exactly from the candidate list")
    reason: str = Field(description="A brief one-sentence explanation of why this matches the query")


class PaperPicks(BaseModel):
    """Step 3 of a paper search: the best candidates, ranked."""
    items: List[PaperPick] = Field(description=f"The best matching candidates, most relevant first, at most {PAPER_RESULTS_SHOWN}")
