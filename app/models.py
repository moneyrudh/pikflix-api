from typing import List, Literal, Optional, Dict, Any, Union
from enum import Enum
from pydantic import BaseModel, Field, model_validator
from datetime import date, datetime


# The watch subset of `MediaType`. The docstring is part of the schema sent to Claude
# (see the snapshot test), so notes go here instead.
class ContentType(str, Enum):
    """What a piece of content IS — movie or show."""
    MOVIE = "movie"
    SHOW = "show"


class ContentTypeMode(str, Enum):
    """What the user is requesting — movie, show, or both."""
    MOVIE = "movie"
    SHOW = "show"
    BOTH = "both"


class MediaType(str, Enum):
    """Every kind of item PikFlix can recommend. Mirrors the `pikflix.media_type` DB enum."""
    MOVIE = "movie"
    SHOW = "show"
    BOOK = "book"
    PAPER = "paper"


class Shelf(str, Enum):
    WATCH = "watch"
    READ = "read"


SHELF_MEDIA: Dict[Shelf, frozenset[MediaType]] = {
    Shelf.WATCH: frozenset({MediaType.MOVIE, MediaType.SHOW}),
    Shelf.READ: frozenset({MediaType.BOOK, MediaType.PAPER}),
}

# Media types the search pipeline can serve today. Books and papers join in later phases.
SUPPORTED_MEDIA: frozenset[MediaType] = SHELF_MEDIA[Shelf.WATCH]


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


class TMDBVideo(BaseModel):
    """One entry of TMDB's /videos results (via append_to_response=videos)."""
    key: str
    site: str
    type: str
    name: Optional[str] = None
    official: bool = False
    iso_639_1: Optional[str] = None
    size: Optional[int] = None
    published_at: Optional[datetime] = None


class Trailer(BaseModel):
    """The single video chosen to show for a movie/show. `key` is the YouTube video id."""
    key: str
    name: Optional[str] = None
    official: bool = False
    language: Optional[str] = None


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
    trailer: Optional[Trailer] = None
    videos_checked_at: Optional[datetime] = None
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
    trailer: Optional[Trailer] = None
    videos_checked_at: Optional[datetime] = None
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
    media_types: List[MediaType] = Field(default_factory=list, description="Non-empty subset of one shelf")
    content_type: Optional[ContentTypeMode] = Field(None, description="Legacy: mapped to media_types when those are absent")
    history: Optional[List[ConversationTurn]] = None
    web_search: bool = False

    @model_validator(mode="after")
    def _resolve_media_types(self) -> "UserQuery":
        if "media_types" in self.model_fields_set:
            if not self.media_types:
                raise ValueError("media_types must not be empty")
        else:
            mode = self.content_type or ContentTypeMode.MOVIE
            self.media_types = (
                [MediaType.MOVIE, MediaType.SHOW] if mode == ContentTypeMode.BOTH else [MediaType(mode.value)]
            )
        self.media_types = list(dict.fromkeys(self.media_types))  # dedupe, keep order

        requested = frozenset(self.media_types)
        if not any(requested <= shelf for shelf in SHELF_MEDIA.values()):
            raise ValueError("media_types must all come from the same shelf (watch or read)")
        unsupported = requested - SUPPORTED_MEDIA
        if unsupported:
            raise ValueError(f"not supported yet: {', '.join(sorted(m.value for m in unsupported))}")
        return self

    @property
    def watch_mode(self) -> ContentTypeMode:
        """The watch request as the prompt layer's mode (movie, show, or both)."""
        if len(self.media_types) > 1:
            return ContentTypeMode.BOTH
        return ContentTypeMode(self.media_types[0].value)


class FetchRequest(BaseModel):
    """Item that needs to be fetched from TMDB — either fresh or cache-expired."""
    title: str
    year: Optional[int] = None
    reason: Optional[str] = None
    id: Optional[int] = None  # Present when cache had an expired entry


class CacheResult(BaseModel):
    """Return type from Supabase cache lookup."""
    found: List[Dict[str, Any]] = []
    to_fetch: List[FetchRequest] = []


class ProviderRequest(BaseModel):
    content_id: int
    content_type: ContentType = ContentType.MOVIE
    region: str


class ProviderResponse(BaseModel):
    id: int
    results: Dict[str, Any]


# --- Recommendation stream (NDJSON). One model per event type; each line is `event.model_dump_json()`.

class InitEvent(BaseModel):
    type: Literal["init"] = "init"
    query: str
    media_types: List[MediaType]
    content_type: ContentTypeMode  # legacy: what the current client reads


class ContentEvent(BaseModel):
    """Legacy per-result event. Sent alongside `ItemEvent` until the client reads `item`."""
    type: Literal["content"] = "content"
    content_type: ContentType
    reason: Optional[str] = None
    data: Union[Movie, Show]


class ItemEvent(BaseModel):
    """One result: the item plus why Claude picked it (`reason` describes the pick, not the item)."""
    type: Literal["item"] = "item"
    media_type: MediaType
    reason: Optional[str] = None
    data: Union[Movie, Show]


class DoneEvent(BaseModel):
    """Last event of a successful stream."""
    type: Literal["done"] = "done"
    count: int     # results sent
    dropped: int   # picks that couldn't be resolved or failed


class ErrorEvent(BaseModel):
    """Last event of a failed stream. Results sent before it are still valid."""
    type: Literal["error"] = "error"
    message: str
