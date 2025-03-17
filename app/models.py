from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import date, datetime


class Genre(BaseModel):
    id: int
    name: str


class ProductionCompany(BaseModel):
    id: int
    logo_path: Optional[str] = None
    name: str
    origin_country: str


class ProductionCountry(BaseModel):
    iso_3166_1: str
    name: str


class SpokenLanguage(BaseModel):
    english_name: str
    iso_639_1: str
    name: str


class Collection(BaseModel):
    id: int
    name: str
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None


class Movie(BaseModel):
    id: int
    imdb_id: Optional[str] = None
    title: str
    original_title: Optional[str] = None
    original_language: Optional[str] = None
    overview: Optional[str] = None
    tagline: Optional[str] = None
    status: Optional[str] = None
    release_date: Optional[date] = None
    adult: Optional[bool] = False
    budget: Optional[int] = 0
    revenue: Optional[int] = 0
    runtime: Optional[int] = None
    vote_average: Optional[float] = 0.0
    vote_count: Optional[int] = 0
    popularity: Optional[float] = 0.0
    video: Optional[bool] = False
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    homepage: Optional[str] = None
    belongs_to_collection: Optional[Collection] = None
    genres: Optional[List[Genre]] = []
    production_companies: Optional[List[ProductionCompany]] = []
    production_countries: Optional[List[ProductionCountry]] = []
    spoken_languages: Optional[List[SpokenLanguage]] = []
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserQuery(BaseModel):
    query: str = Field(..., description="Natural language description of movie requirements")


class MovieRecommendationResponse(BaseModel):
    recommendations: List[Movie]
    query: str