from typing import List, Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
from datetime import date, datetime


class ContentType(str, Enum):
    """What a piece of content IS — movie or show."""
    MOVIE = "movie"
    SHOW = "show"


class ContentTypeMode(str, Enum):
    """What the user is requesting — movie, show, or both."""
    MOVIE = "movie"
    SHOW = "show"
    BOTH = "both"


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


class Network(BaseModel):
    id: int
    logo_path: Optional[str] = None
    name: str
    origin_country: str


class Creator(BaseModel):
    id: int
    credit_id: Optional[str] = None
    name: str
    profile_path: Optional[str] = None


class Season(BaseModel):
    air_date: Optional[str] = None
    episode_count: Optional[int] = None
    id: int
    name: Optional[str] = None
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    season_number: Optional[int] = None
    vote_average: Optional[float] = None


class EpisodeInfo(BaseModel):
    id: int
    name: Optional[str] = None
    overview: Optional[str] = None
    vote_average: Optional[float] = None
    vote_count: Optional[int] = None
    air_date: Optional[str] = None
    episode_number: Optional[int] = None
    episode_type: Optional[str] = None
    production_code: Optional[str] = None
    runtime: Optional[int] = None
    season_number: Optional[int] = None
    show_id: Optional[int] = None
    still_path: Optional[str] = None


class Show(BaseModel):
    id: int
    name: str
    original_name: Optional[str] = None
    original_language: Optional[str] = None
    overview: Optional[str] = None
    tagline: Optional[str] = None
    status: Optional[str] = None
    first_air_date: Optional[date] = None
    last_air_date: Optional[date] = None
    number_of_seasons: Optional[int] = None
    number_of_episodes: Optional[int] = None
    episode_run_time: Optional[List[int]] = []
    adult: Optional[bool] = False
    vote_average: Optional[float] = 0.0
    vote_count: Optional[int] = 0
    popularity: Optional[float] = 0.0
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    homepage: Optional[str] = None
    genres: Optional[List[Genre]] = []
    production_companies: Optional[List[ProductionCompany]] = []
    production_countries: Optional[List[ProductionCountry]] = []
    spoken_languages: Optional[List[SpokenLanguage]] = []
    networks: Optional[List[Network]] = []
    created_by: Optional[List[Creator]] = []
    origin_country: Optional[List[str]] = []
    languages: Optional[List[str]] = []
    seasons: Optional[List[Season]] = []
    last_episode_to_air: Optional[EpisodeInfo] = None
    next_episode_to_air: Optional[EpisodeInfo] = None
    in_production: Optional[bool] = None
    type: Optional[str] = None
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True


class RecommendationSummary(BaseModel):
    title: str
    year: Optional[int] = None
    reason: Optional[str] = None


class ConversationTurn(BaseModel):
    query: str
    recommendations: List[RecommendationSummary] = []


class UserQuery(BaseModel):
    query: str = Field(..., description="Natural language description of what to watch")
    content_type: ContentTypeMode = ContentTypeMode.MOVIE
    history: Optional[List[ConversationTurn]] = None

class ProviderRequest(BaseModel):
    content_id: int
    content_type: ContentType = ContentType.MOVIE
    region: str


class ProviderResponse(BaseModel):
    id: int
    results: Dict[str, Any]
