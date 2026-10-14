# PikFlix: Watch & Read — Technical Design

| | |
|---|---|
| **Status** | Draft for review (rev 2: current-state section updated to reflect `a2d9322`) |
| **Date** | 2026-10-11 |
| **Repos** | `pikflix-api` (this repo), `pikflix-client` |
| **Scope** | Trailers, books, papers, deep links, Watch/Read tabs, and the conversational media agent |

---

## 1. Summary

PikFlix today turns a natural-language request into 9 movies and/or shows. This design grows it into a **media companion** across two shelves:

- **Watch**: movies and shows, now with trailers and deep links into streaming services.
- **Read**: books and research papers, with legal links to buy, borrow, preview, or download open-access PDFs.

It then adds a **conversational agent** on top. The agent is a chat interface whose replies are a list of typed blocks (text, movie, show, book, paper, question), backed by tools over the same data layer.

The work ships in seven phases. Each phase is useful on its own, and each is built so the agent (phase 6) mostly reuses what came before.

### Goals
1. Trailers on movie and show results, in a way that doesn't slow down the results grid.
2. Search books and papers with the same "describe what you want" experience.
3. Show where to watch with deep links, and where to read with legal links, everything cached.
4. One shared data model (identity, items, availability) used by search results *and* agent replies.
5. A provider registry so link sources (Streaming Availability now, Watchmode later) plug in without touching callers.
6. A chat agent that can search, ask clarifying questions, identify half-remembered titles, and hand off to "where to watch".

### Non-goals (for now)
- User accounts, bookmarks, or watch history (needs Supabase Auth; tracked separately).
- Native mobile apps. Web links are enough for a web app, and app deep links are a later upgrade.
- Any link to unlicensed copies. Read links come only from publishers, libraries, stores, and open-access sources (§9.4).
- A second LLM provider in production. The interface is designed for it (§12.8), and only Claude is implemented.

---

## 2. Current state

### 2.1 Data flow
```
client ── POST /api/recommendations {query, content_type, history, web_search}
  └─ AnthropicService.get_recommendations()      # streams ContentRecommendation (title, year, reason, content_type)
      └─ per item: Supabase cache (title + year) → TMDB search/details on miss
          └─ NDJSON: {"type":"content","content_type":"movie","data":{...}}
          └─ background: save item, fetch + save TMDB watch providers
client ── POST /api/providers {content_id, content_type, region}   # TMDB/JustWatch provider lists
```

### 2.2 Problems to fix along the way
These are existing issues that the new work would make worse. P1–P4 and half of P5 were fixed in `a2d9322` ("Fix event-loop blocking and caching bugs"). The rest are assigned to a phase.

| # | Issue | Where | Impact | Status |
|---|---|---|---|---|
| P1 | The **sync** `anthropic.Anthropic` client was iterated inside an `async` generator | `anthropic_service.py` | Blocked the event loop for the whole stream | **Fixed** (`a2d9322`): `AsyncAnthropic`, one shared instance |
| P2 | The **sync** Supabase client was called from `async` methods | `supabase_service.py` | Each query blocked the loop | **Fixed** (`a2d9322`): `acreate_client`, created lazily under a lock |
| P3 | A new `httpx.AsyncClient` (and, per request, a new Anthropic client) was created on every call | `tmdb_service.py`, endpoints | No connection reuse | **Fixed** (`a2d9322`): one client per service via `@cache` getters, closed in `lifespan` |
| P4 | The provider cache never expired | `supabase_service.py` | Stale "where to watch" data | **Fixed** (`a2d9322`): 24h `PROVIDER_CACHE_DURATION` |
| P5 | The streaming brace-matcher assumes items sit at depth 2 (escaped quotes were also broken) | `anthropic_service.py` | Can't parse items containing nested objects, so it can't support block types with lists or refs | Escapes **fixed** (`a2d9322`). Nesting: **Phase 0** (§6.6) |
| P6 | The cache is keyed by exact title + year. (`CLAUDE.md` marks ID-based matching as done, but `get_content_by_titles` still only matches titles) | `supabase_service.py` | Misses on title variants ("Se7en" vs "Seven"), and duplicate lookups | **Phase 1** (ID-first resolution). Correct `CLAUDE.md` |
| P7 | Picks are handled one at a time: each pick's cache lookup and TMDB fetch must finish before the next pick starts | `recommendations.py` | Time-to-first-card and total latency grow with every slow lookup | **Phase 0** (§12.7) |
| P8 | Errors from the LLM stream are logged and swallowed | `anthropic_service.py` | The client sees an empty or short stream and can't tell a failure from "no results" | **Phase 1** (`error`/`done` events, §6.5) |

---

## 3. Roadmap

| Phase | Deliverable | Backend | Frontend | Done when |
|---|---|---|---|---|
| **0** | Trailers + stream fixes | `append_to_response=videos`, trailer selection, nested-object stream parser (P5), `movies` → `items` schema key, concurrent per-pick handling (P7) | Click-to-play trailer facade | Every movie/show card with a trailer plays it. Time-to-first-card is no worse than today, and total stream time is measurably lower |
| **1** | Shared data model + **Books** | `MediaType`, `MediaRef`, item models, Open Library resolver, `books` table, generic cache helper, P6, P8 | `Book` type + card + details | A "Read → Books" search streams 9 verified books |
| **2** | **Papers** | Retrieve-then-rank pipeline (Semantic Scholar), `papers` table | `Paper` type + card + details | 9 real papers (each has a resolvable ID) per search |
| **3** | **Streaming Availability** + caching | `WatchProvider` registry, Streaming Availability provider, `watch_availability` table, `/api/availability/watch` | "Where to watch" with deep links | Details panel shows deep links. Cache hit rate is visible in logs |
| **4** | **Read links** + caching | `ReadLinkProvider` registry (Open Library, Google Books, Unpaywall, arXiv), `read_availability` table | "Where to read" section, book preview | Books show buy/borrow/preview. Papers show open-access PDF links when they exist |
| **5** | **Watch / Read tabs** | `media_types` request param, per-type prompts | Tabs + per-tab toggle, URL state | Both shelves fully usable. The old `content_type` param is still accepted |
| **6** | **Media agent** | `/api/chat`, agent loop, tools, `AgentReply` blocks, sessions, evals | Chat UI (`/chat`) rendering blocks | Agent passes the eval thresholds in §11.9 |

Phases 1 and 2 add new search *pipelines* behind the existing endpoint. Phase 5 is mostly frontend. If you'd like books to be usable before the tabs exist, phases 1–2 can ship behind a temporary toggle option.

---

## 4. Domain model

This is the core of the design. Everything else (search, links, agent) speaks these types.

### 4.1 Principles
1. **Identity is separate from data.** A `MediaRef` (type + stable external ID) identifies a thing, and items are *hydrated* from a ref. The agent and caches pass refs around, and only the API edge sends full items.
2. **LLM schemas are separate from API models.** As today (`schemas.py` vs `models.py`), the LLM never outputs posters, URLs, or metadata. It outputs titles to resolve (search) or refs (agent), and the backend fills in the rest. This stops made-up links and IDs from reaching users.
3. **Enums, not strings** (per `CLAUDE.md`). `MediaType` replaces the movie/show-only `ContentType`.

### 4.2 Enums
```python
class MediaType(str, Enum):
    MOVIE = "movie"
    SHOW = "show"
    BOOK = "book"
    PAPER = "paper"

class Shelf(str, Enum):
    WATCH = "watch"
    READ = "read"

SHELF_MEDIA: dict[Shelf, tuple[MediaType, ...]] = {
    Shelf.WATCH: (MediaType.MOVIE, MediaType.SHOW),
    Shelf.READ: (MediaType.BOOK, MediaType.PAPER),
}
```

**Migration from `ContentType` / `ContentTypeMode`:**
- `ContentType` becomes an alias of the watch subset during the transition. DB enum `pikflix.content_type` is renamed to `pikflix.media_type` (`ALTER TYPE ... RENAME TO`, metadata only, and existing columns follow automatically) and gains `book` and `paper` (`ALTER TYPE ... ADD VALUE`). That keeps the DB name the same as the Python name.
- `ContentTypeMode.BOTH` is replaced by `UserQuery.media_types: list[MediaType]` (a non-empty subset of a single shelf). "Both" becomes `[movie, show]`. A list generalizes to the Read shelf without a second `BOTH` enum.
- `UserQuery` keeps accepting the legacy `content_type` field for one release and maps it to `media_types` with a validator, so the deployed frontend keeps working while the API ships first.

### 4.3 Identity
```python
class MediaRef(BaseModel):
    """Stable identity of a media item. `id` is the source system's ID, as a string."""
    type: MediaType
    id: str   # movie/show: TMDB id ("603"); book: Open Library work ("OL45883W"); paper: Semantic Scholar paperId

    @property
    def key(self) -> str:            # cache / dedupe key, e.g. "movie:603"
        return f"{self.type.value}:{self.id}"
```

| Type | `id` source | Why |
|---|---|---|
| movie / show | TMDB id | What every existing table and API uses. Streaming Availability accepts `movie/<tmdb_id>` and `tv/<tmdb_id>` directly |
| book | Open Library **work** key (`OL…W`) | Work-level, so it groups editions. Free, with covers. ISBNs are kept on the item for Google Books lookups |
| paper | Semantic Scholar `paperId` | Always present in S2 results, unlike DOI (missing for many preprints). DOI and arXiv IDs are stored as columns for link lookups and dedupe |

**Where `str` IDs meet `int` columns.** `movies.id`, `shows.id` and `providers.content_id` are integers (TMDB IDs), and `Movie.id`/`Show.id` stay `int`. `MediaRef.id` is always a string. The conversion happens only inside `TmdbResolver` (`MediaRef(id=str(tmdb_id))` on resolve, `int(ref.id)` on hydrate) and in the watch providers that call TMDB-keyed APIs. `CacheStore` gets typed keys from its caller and never converts. The new `books`, `papers` and `availability` tables use `text` IDs.

### 4.4 Items (API models)
`Movie` and `Show` stay as they are (they mirror TMDB), plus two additions. Books and papers get new models. The API sends items as a **discriminated union on `media_type`**:

```python
class Trailer(BaseModel):
    site: Literal["YouTube"] = "YouTube"
    key: str                      # YouTube video id
    name: str | None = None
    official: bool = False
    language: str | None = None   # iso_639_1

class Movie(BaseModel):           # existing fields unchanged …
    media_type: Literal[MediaType.MOVIE] = MediaType.MOVIE
    trailer: Trailer | None = None

class Show(BaseModel):            # existing fields unchanged …
    media_type: Literal[MediaType.SHOW] = MediaType.SHOW
    trailer: Trailer | None = None

class Author(BaseModel):
    name: str
    id: str | None = None         # OL author key or S2 authorId

class Book(BaseModel):
    media_type: Literal[MediaType.BOOK] = MediaType.BOOK
    id: str                       # OL work key
    title: str
    subtitle: str | None = None
    authors: list[Author] = []
    first_publish_year: int | None = None
    description: str | None = None
    cover_id: int | None = None   # → https://covers.openlibrary.org/b/id/{cover_id}-L.jpg
    isbns: list[str] = []         # ISBN-13 first; used for Google Books lookups
    subjects: list[str] = []
    page_count: int | None = None     # number_of_pages_median
    edition_count: int | None = None
    ia_ids: list[str] = []            # Internet Archive identifiers (borrow/read)
    ebook_access: str | None = None   # Open Library: "public" | "borrowable" | "printdisabled" | "no_ebook"
    last_updated: datetime | None = None

class Paper(BaseModel):
    media_type: Literal[MediaType.PAPER] = MediaType.PAPER
    id: str                       # Semantic Scholar paperId
    title: str
    authors: list[Author] = []
    year: int | None = None
    publication_date: date | None = None
    venue: str | None = None
    abstract: str | None = None
    tldr: str | None = None       # S2 machine-generated one-liner
    doi: str | None = None
    arxiv_id: str | None = None
    citation_count: int | None = None
    fields_of_study: list[str] = []
    open_access_pdf: str | None = None   # from S2 openAccessPdf.url (Unpaywall may add better links later)
    last_updated: datetime | None = None

MediaItem = Annotated[Union[Movie, Show, Book, Paper], Field(discriminator="media_type")]

class SearchResultItem(BaseModel):
    """What the search stream sends per result: the item plus why it was picked."""
    item: MediaItem
    reason: str
```

Notes:
- `reason` moves **off** the item models into `SearchResultItem`. Today it is written into the cached dict and stripped again before saving (`_prepare_for_db`). Keeping it separate removes that workaround (`CLAUDE.md`: "fix root causes").
- The API-side union uses Pydantic's `discriminator`, which is fine for FastAPI/OpenAPI. The **LLM-side** schemas must not (see §4.6).

### 4.5 Availability (links)
One envelope covers both shelves, so the frontend and the agent treat "where to get it" uniformly:

```python
class LinkKind(str, Enum):
    # watch
    SUBSCRIPTION = "subscription"
    FREE = "free"
    RENT = "rent"
    BUY = "buy"
    ADDON = "addon"
    # read
    PREVIEW = "preview"           # Google Books sample / embedded viewer
    BORROW = "borrow"             # Internet Archive / library lending
    OPEN_ACCESS = "open_access"   # legal free PDF or HTML (Unpaywall, arXiv, S2)
    STORE = "store"               # buy a book (Google Books buy link; more stores later)
    LANDING = "landing"           # publisher / DOI page

class Price(BaseModel):
    amount: Decimal
    currency: str
    formatted: str | None = None

class AvailabilityOption(BaseModel):
    kind: LinkKind
    service_id: str               # "netflix", "apple", "internet_archive", "unpaywall", …
    service_name: str
    url: str                      # web link (deep link where available)
    app_url: str | None = None    # iOS/Android deep link (Watchmode paid tier, later)
    price: Price | None = None
    quality: str | None = None    # "hd" | "uhd" | …
    expires_on: date | None = None
    detail: str | None = None     # e.g. "Accepted manuscript, CC-BY" or "Season 2"

class Availability(BaseModel):
    ref: MediaRef
    country: str | None           # ISO 3166-1 alpha-2. None for region-independent read links
    options: list[AvailabilityOption]
    sources: list[str]            # which providers contributed, e.g. ["streaming_availability"]
    attribution: list[str] = []   # strings the UI must show, e.g. "Data by JustWatch"
    fetched_at: datetime
```

### 4.6 LLM schemas (`schemas.py`)
The rules for every schema sent as `output_config.format`:
- **Unions are `anyOf` with a `Literal` tag field.** Do *not* use `Field(discriminator=...)` in LLM schemas: Pydantic emits `oneOf` + `discriminator`, and Claude's structured outputs document `anyOf`, `const`, and `enum` as supported but don't list `oneOf`. A plain `Union[...]` of models with a `Literal` tag emits `anyOf`, and Pydantic still selects the right model when validating.
- Keep running every schema through `anthropic.transform_schema(...)`, as the code already does. It strips unsupported constraints and validates them client-side.
- Add a unit test that snapshots each transformed schema and asserts it contains no `oneOf`, `discriminator`, or numeric/string-length constraints. Schemas are part of the contract, so a refactor shouldn't silently change them.

Search-phase schemas (phases 1–2):
```python
class WatchPick(BaseModel):                  # replaces ContentRecommendation (renamed field)
    title: str
    year: int
    reason: str
    media_type: Literal[MediaType.MOVIE, MediaType.SHOW]

class BookPick(BaseModel):
    title: str
    author: str = Field(description="Primary author's full name")
    year: int | None = Field(description="First publication year if known")
    reason: str

class PaperQueries(BaseModel):               # step 1 of the paper pipeline
    queries: list[str] = Field(description="1-3 keyword search queries that together cover the request")

class PaperPick(BaseModel):                  # step 2: rank real candidates
    paper_id: str = Field(description="paperId copied exactly from the candidate list")
    reason: str

class WatchPicks(BaseModel):
    items: list[WatchPick] = Field(description="Exactly 9 picks")
# BookPicks / PaperPicks have the same shape.
```
Every wrapper uses the same top-level key, `items`, which the streaming parser in §6.6 relies on. (A `Generic[T]` Pydantic model works too, but three explicit classes give clearer JSON schema titles.)

Today's wrapper, `ContentRecommendations`, uses the key `movies` (`schemas.py`). Renaming it to `items` changes the schema sent to Claude, so it ships in **Phase 0** together with the new parser, and the schema snapshot test is added in the same change. The rename is internal to the backend: the NDJSON sent to the client doesn't change.

---

## 5. Data sources

| Media | Search / details | Links | Trailer / preview | Keys | Limits & obligations |
|---|---|---|---|---|---|
| Movie / Show | TMDB (existing) | Streaming Availability (Movie of the Night) via RapidAPI. *Later:* Watchmode | TMDB `/videos` (YouTube keys) | `TMDB_READ_ACCESS_TOKEN`, `STREAMING_AVAILABILITY_API_KEY` | TMDB: credit JustWatch wherever watch-provider data appears. Streaming Availability free tier: 100 requests/day |
| Book | Open Library `search.json` + `/works/{id}.json` (description) | Open Library / Internet Archive (borrow/read), Google Books (preview, buy) | Google Books embedded viewer (`accessInfo.embeddable`) | `GOOGLE_BOOKS_API_KEY` (optional, recommended in prod) | Open Library asks clients to identify themselves with a descriptive `User-Agent` and to stay polite. Check their current rate guidance |
| Paper | Semantic Scholar Graph API `/paper/search` (OpenAlex as a later fallback) | S2 `openAccessPdf`, Unpaywall `v2/{doi}?email=`, arXiv | Abstract + S2 `tldr`. First-page thumbnail is optional (§8.3) | `SEMANTIC_SCHOLAR_API_KEY` (free, request one), `UNPAYWALL_EMAIL` | Without a key, S2 shares a global rate limit across all anonymous users, which is unreliable in prod. Unpaywall requires an email param |

New env vars (all optional at startup except where a phase requires them; validate in `config.py` like the existing ones):
```
STREAMING_AVAILABILITY_API_KEY   # phase 3
GOOGLE_BOOKS_API_KEY             # phase 4
SEMANTIC_SCHOLAR_API_KEY         # phase 2
UNPAYWALL_EMAIL                  # phase 4
OPENLIBRARY_USER_AGENT           # phase 1, e.g. "PikFlix/1.0 (contact@…)"
```

---

## 6. Search pipelines

### 6.1 Shape
All four media types follow the same steps:

```
query ─► LLM (structured, streamed) ─► candidates ─► resolve → verify → hydrate (cache-first) ─► stream SearchResultItem
                                                     └─ background: cache writes
```

The only difference between media types is the **resolver**:

```python
class Resolver(Protocol[Pick]):
    media_type: MediaType
    async def resolve(self, pick: Pick) -> MediaRef | None: ...       # find the real thing, or None
    async def hydrate(self, ref: MediaRef) -> MediaItem | None: ...   # cache-first full item
```

Cache lookups move from **title-based to ID-based** (fixes P6). That means resolve, then look up by ID. The extra search call per pick costs little, because results are cached and the search is the same request TMDB resolution already makes.

### 6.2 Watch (movie / show)
Unchanged behavior, refactored onto `Resolver`:
- `TmdbResolver.resolve`: the existing search with type fallback (movie ↔ show) → `MediaRef`.
- `TmdbResolver.hydrate`: `movies`/`shows` cache by ID → TMDB details with `append_to_response=videos` on miss (§8.1).

### 6.3 Books
1. Claude streams `BookPick`s (title, author, year, reason).
2. `OpenLibraryResolver.resolve`: `GET search.json?title=…&author=…&fields=key,title,author_name,first_publish_year,cover_i,isbn,ia,ebook_access,edition_count,number_of_pages_median,subject&limit=5`.
3. **Verify the match** before accepting it. Book titles collide constantly:
   - normalized title similarity ≥ 0.85 (casefold, strip punctuation and subtitles, `difflib.SequenceMatcher` is enough)
   - **and** an overlap between the pick's author surname and `author_name`
   - **and** `|first_publish_year - pick.year| ≤ 2` when both are known (republications drift)
   - Pick the best-scoring doc. If none pass, **drop the pick** and log it. Never show an unverified book.
4. `hydrate`: `books` cache → on miss, assemble from the search doc plus `GET /works/{id}.json` for `description` (which is a string *or* `{"type": "/type/text", "value": …}`, so handle both).

Dropping picks means a search can return fewer than 9 results. Two mitigations:
- (a) ask Claude for 12 and stop streaming after 9 verified results
- (b) log the drop rate per search as a quality metric

Start with (a).

### 6.4 Papers: retrieve, then rank
LLMs make up plausible-looking papers, with fake titles, authors, and DOIs. So papers **invert** the pipeline: search for real candidates first, then let Claude choose among them.

1. **Plan:** Claude (non-streamed, small) → `PaperQueries` (1–3 keyword queries). Skip this when the user's query is already short and keyword-like.
2. **Retrieve:** for each query, `GET /graph/v1/paper/search?query=…&limit=20&fields=paperId,externalIds,title,abstract,year,venue,authors,citationCount,openAccessPdf,tldr,publicationDate,fieldsOfStudy`, in parallel. Merge and dedupe by DOI, then arXiv ID, then `paperId`. Cap at ~40 candidates.
3. **Rank:** Claude (streamed) gets the user query plus a compact candidate list (`paperId | title | year | venue | citations | first ~300 chars of abstract`) → `PaperPicks`.
4. **Validate:** drop any `paper_id` not in the candidate set. That should be impossible, but this check is what guarantees it.
5. **Hydrate** from the candidate data already fetched (no extra call), and upsert into `papers` in the background.

Cost note: one extra small LLM call and 1–3 S2 calls per paper search. Paper searches are lower volume than watch searches, so this is fine.

### 6.5 Streaming protocol (NDJSON v2)
```jsonc
{"type": "init",  "query": "…", "media_types": ["book", "paper"]}
{"type": "item",  "media_type": "book", "reason": "…", "data": { /* Book */ }}
{"type": "done",  "count": 9, "dropped": 1}
{"type": "error", "message": "…"}           // terminal; stream ends
```
- The current `{"type":"content", "content_type":…}` events stay alongside `item` for watch results until `pikflix-client` migrates. Then they're removed.
- `done` is new. Today the client infers the end of results from stream close, and an explicit end event makes errors distinguishable from "fewer results".

### 6.6 Streaming parser (fixes P5)
Replace the depth-2 brace-matcher with a small incremental scanner that yields each completed element of the top-level `items` array:
- Keep tracking `in_string` **and** `escape`, which `a2d9322` already added to the current matcher. Move that logic into the new module unchanged.
- Track depth *relative to the array start*. An element is complete when depth returns to the array level, which works for nested objects. That matters in phase 6, where blocks contain lists (`QuestionBlock.options`).
- Yield `T.model_validate(json.loads(element))`, and log and skip elements that fail validation.

The `CLAUDE.md` rule says not to replace the brace matcher with buffer scanning or delimiter splitting. This change keeps that rule: it is the same character-by-character state machine, with escape and nesting support. Update `CLAUDE.md` to describe the generalized matcher. It lives in `app/services/llm_stream.py`, used by every pipeline and by the agent.

---

## 7. Links: provider registries

### 7.1 Watch providers (phase 3)
```python
class WatchProvider(Protocol):
    name: str                         # "streaming_availability"
    description: str                  # capability text; becomes the agent tool description in phase 6
    capabilities: frozenset[str]      # {"web_links", "episode_links", "app_links", "prices"}
    async def lookup(self, ref: MediaRef, country: str, *, granularity: Granularity = Granularity.TITLE) -> Availability: ...

WATCH_PROVIDERS: list[WatchProvider] = [StreamingAvailabilityProvider()]   # later: + WatchmodeProvider()
```

**`StreamingAvailabilityProvider`**
- `GET https://streaming-availability.p.rapidapi.com/shows/{movie|tv}/{tmdb_id}?country={cc}&series_granularity={show|season|episode}&output_language=en`, with headers `X-RapidAPI-Key` and `X-RapidAPI-Host`.
- Maps `streamingOptions[cc][]` → `AvailabilityOption`:
  - `service` → id/name
  - `type` (`subscription|free|rent|buy|addon`) → `LinkKind`
  - `link` → `url`
  - `price.{amount,currency,formatted}` → `Price`
  - `quality`, `expiresOn`
- Search cards and the details panel use `series_granularity=show`, which keeps the payload small. `season`/`episode` are only requested by the agent (phase 6, e.g. "it's S2E5").
- Confirm whether the free tier requires attribution, and add it to `Availability.attribution` if so.

**`AvailabilityService`** (code, not the LLM, handles the plumbing):
- `get_watch(ref, country, providers=None)`: cache → provider(s) → cache write.
- **Fallback chain:** if the preferred provider errors, times out, or is over quota, try the next registered provider. With one provider today, the fallback is "return TMDB's JustWatch-based list without deep links" (the existing `/api/providers` data), so the UI always has *something*.
- **Quota guard:** a per-provider daily counter (Supabase row or in-process with a reset time). Stop calling a provider once it hits ~90% of quota and go straight to fallback. At 100 requests/day, this matters.

**Fetch on demand, never per card.** Nine cards per search would use the free tier in about 11 searches. Links load when the details panel opens (the same trigger `/api/providers` uses today), and are cached per `(ref, country, provider)`.

### 7.2 Read link providers (phase 4)
```python
class ReadLinkProvider(Protocol):
    name: str
    media_types: frozenset[MediaType]
    async def lookup(self, item: Book | Paper) -> list[AvailabilityOption]: ...
```
Unlike watch providers, read providers **all run** and their results merge. They complement each other rather than compete.

| Provider | Applies to | Produces |
|---|---|---|
| `OpenLibraryLinks` | book | `BORROW`/`OPEN_ACCESS` → `https://archive.org/details/{ia_id}` when `ebook_access` is `borrowable`/`public`. `LANDING` → `https://openlibrary.org/works/{id}` |
| `GoogleBooksLinks` | book | `volumes?q=isbn:{isbn}` → `PREVIEW` (`previewLink`/`webReaderLink` when `viewability != NO_PAGES`), `STORE` (`saleInfo.buyLink` + `retailPrice` when `FOR_SALE`). Records `accessInfo.embeddable` for the in-app sample viewer |
| `UnpaywallLinks` | paper (with DOI) | `GET api.unpaywall.org/v2/{doi}?email=…` → `OPEN_ACCESS` from `best_oa_location.url_for_pdf` (or `url`), with `version` + `license` in `detail` |
| `ArxivLinks` | paper (with arXiv ID) | `OPEN_ACCESS` → `https://arxiv.org/pdf/{id}` and abstract page |
| `DoiLanding` | paper (with DOI) | `LANDING` → `https://doi.org/{doi}` |

Dedupe merged options by normalized URL. Ordering: open access, then preview, then borrow, then store, then landing.

### 7.3 Endpoints
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/recommendations/` | `UserQuery` (+ `media_types`) | NDJSON v2 stream |
| POST | `/api/availability/watch` | `{ref: MediaRef, country: str}` | `Availability` |
| POST | `/api/availability/read` | `{ref: MediaRef}` | `Availability` |
| POST | `/api/providers/` | *(deprecated after phase 3; kept until the client switches)* | existing shape |
| POST | `/api/chat` | phase 6 (§11) | NDJSON stream of agent events |

---

## 8. Trailers and previews

### 8.1 Movie / show trailers (phase 0)
- Fetch videos in the details call you already make: `GET /movie/{id}?append_to_response=videos` (same for `/tv/{id}`). That's no extra request.
- **Selection** (`pick_trailer(videos, lang)`): keep `site == "YouTube"` and `type == "Trailer"`. If there are none, fall back to `type == "Teaser"`. Rank by `official` desc, language match, `size` desc, then `published_at` **asc** (earliest first). Take the first, or `None`.
  - Why earliest: checked against real TMDB data, the newest upload is usually a re-release or home-video promo ("25th Anniversary Trailer", "B&W Version", "Own it on Blu-ray"). The earliest is the original campaign trailer. Results: The Matrix → "Official 4K Trailer", Parasite → "Official Int'l Main Trailer", Dune: Part Two → "Official Trailer 2".
- The raw `videos` key is replaced by `trailer` + `videos_checked_at` in the details payload before it reaches the cache. The raw TMDB dict is upserted as-is, so an unknown key would make the save fail.
- **Storage:** `trailer jsonb` + `videos_checked_at timestamptz` columns on `movies` and `shows`. Rows cached before this change have `videos_checked_at IS NULL` and are refreshed once on their next cache hit (one TMDB call each), which backfills over time without a migration job.
- **Frontend:** *never* render one iframe per card. Use a click-to-play facade:
  - The card keeps its poster (a 16:9 YouTube thumbnail doesn't fit the 2:3 card) and shows a small "▶ Trailer" badge when `trailer` is set.
  - The details panel's 16:9 hero is the player: the backdrop plus a "Play trailer" button. On click it swaps in `<iframe src="https://www.youtube-nocookie.com/embed/{key}?autoplay=1&rel=0">`. Nothing loads from YouTube before the click. Playback stops when the panel closes or switches items.

### 8.2 Book previews (phase 4)
When `GoogleBooksLinks` reports `embeddable: true`, the details panel offers **Read a sample** using Google's Embedded Viewer API (client-side script, loaded only on click). Otherwise, show the `PREVIEW` link.

### 8.3 Paper previews (phase 2 + optional)
- v1: abstract + `tldr` + venue, year, and citation count. That's enough to decide whether to open a paper.
- Optional later: **first-page thumbnail**. Rendering open-access PDFs in the browser with pdf.js mostly fails, because publisher servers don't send CORS headers. Do it server-side instead:
  - render page 1 on demand (e.g. PyMuPDF), only for open-access PDFs
  - store the PNG in Supabase Storage keyed by `paper:{id}`
  - serve the cached URL

  This adds a heavy dependency and needs a size and time limit, so it isn't on the critical path.
- Optional later: **"Watch the talk"**, a YouTube Data API search for `"{title}" talk` with results cached. That API has a daily quota, so cache aggressively and only search on demand.

---

## 9. Persistence

### 9.1 Generic cache helper
```python
class CacheStore:
    async def get_fresh(self, table: str, key: dict[str, Any], ttl: timedelta) -> dict[str, Any] | None: ...
    async def put(self, table: str, row: dict[str, Any]) -> None: ...   # upsert, sets last_updated
```
- It sits on the async Supabase client that `SupabaseService` already uses (P2 is fixed). Reuse that client: move `_db()` into `CacheStore` instead of creating a second one.
- One staleness check replaces the copies in `get_content_by_titles` and `get_providers`. Timestamps are UTC (as of `a2d9322`).
- A single `Settings.CACHE_TTL: dict[str, timedelta]` replaces `CACHE_DURATION` and `PROVIDER_CACHE_DURATION`.

### 9.2 TTLs
| Data | TTL | Why |
|---|---|---|
| movies / shows metadata | 7 days | unchanged |
| books / papers metadata | 30 days | effectively static (citation counts drift slowly) |
| watch availability | 24 hours | titles rotate off services, prices change |
| read availability | 7 days | open-access locations and store listings move occasionally |
| TMDB provider lists (legacy) | 24 hours | already in place (P4, `a2d9322`). Moves into `CACHE_TTL` |

### 9.3 Migrations (sketch)
```sql
-- phase 0
ALTER TABLE pikflix.movies ADD COLUMN trailer jsonb, ADD COLUMN videos_checked_at timestamptz;
ALTER TABLE pikflix.shows  ADD COLUMN trailer jsonb, ADD COLUMN videos_checked_at timestamptz;

-- phase 1
ALTER TYPE pikflix.content_type RENAME TO media_type;
ALTER TYPE pikflix.media_type ADD VALUE IF NOT EXISTS 'book';
ALTER TYPE pikflix.media_type ADD VALUE IF NOT EXISTS 'paper';

CREATE TABLE pikflix.books (
  id text PRIMARY KEY,                 -- Open Library work key
  title text NOT NULL,
  subtitle text,
  authors jsonb NOT NULL DEFAULT '[]',
  first_publish_year integer,
  description text,
  cover_id integer,
  isbns jsonb NOT NULL DEFAULT '[]',
  subjects jsonb NOT NULL DEFAULT '[]',
  page_count integer,
  edition_count integer,
  ia_ids jsonb NOT NULL DEFAULT '[]',
  ebook_access text,
  last_updated timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX books_title_idx ON pikflix.books (lower(title));

-- phase 2
CREATE TABLE pikflix.papers (
  id text PRIMARY KEY,                 -- Semantic Scholar paperId
  title text NOT NULL,
  authors jsonb NOT NULL DEFAULT '[]',
  year integer,
  publication_date date,
  venue text,
  abstract text,
  tldr text,
  doi text,
  arxiv_id text,
  citation_count integer,
  fields_of_study jsonb NOT NULL DEFAULT '[]',
  open_access_pdf text,
  last_updated timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX papers_doi_idx ON pikflix.papers (lower(doi)) WHERE doi IS NOT NULL;

-- phase 3/4: one availability table for both shelves
CREATE TABLE pikflix.availability (
  ref_type pikflix.media_type NOT NULL,
  ref_id text NOT NULL,
  country text NOT NULL DEFAULT '',     -- '' for region-independent read links
  provider text NOT NULL,               -- 'streaming_availability', 'unpaywall', …
  options jsonb NOT NULL,               -- list[AvailabilityOption]
  attribution jsonb NOT NULL DEFAULT '[]',
  fetched_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ref_type, ref_id, country, provider)
);

-- quota tracking for metered providers
CREATE TABLE pikflix.provider_usage (
  provider text NOT NULL,
  day date NOT NULL,
  calls integer NOT NULL DEFAULT 0,
  PRIMARY KEY (provider, day)
);
```
Per the existing workflow: `supabase migration new <name>`, `supabase db push --dry-run`, then `supabase db push`. A value added with `ALTER TYPE … ADD VALUE` can't be used in the same transaction that adds it, so keep those statements in their own migration, before anything that inserts `book`/`paper` rows.

### 9.4 Legal and attribution rules (enforced in code)
- Read links are only produced by the providers in §7.2. There is no "find a PDF anywhere" path, and the agent can't produce URLs at all (§11.4).
- `Availability.attribution` is filled in by the providers that require credit (TMDB/JustWatch: "Watch data by JustWatch"), and the UI renders it wherever that data is shown.
- `OPEN_ACCESS` options carry `license`/`version` in `detail` when known, so users can see they're reading a legitimate open-access copy.

---

## 10. Frontend changes (`pikflix-client`)

| Area | Change |
|---|---|
| Types (`types/content.ts`) | Add `MediaType` (`as const`), `Book`, `Paper`, `Trailer`, `Availability`, `AvailabilityOption`. Rename the discriminator from `content_type` to `media_type` (keep reading the old field until the backend drops it). `MediaItem = Movie \| Show \| Book \| Paper` |
| Layout | Top-level **Watch / Read** tabs. Each tab keeps its own toggle (🎬 Movies / 📺 Shows / 🍿 Both, and 📚 Books / 📄 Papers / Both), its own placeholder text, and its own session history. URL state: `?tab=read&types=book,paper&query=…` |
| Cards | Rename `ContentCard` to `MediaCard` with per-type bodies: poster (movie/show), cover (book), title-forward text card with venue/year/citations (paper). Trailer facade on movie/show cards |
| Details panel | Per-type sections: **Where to watch** (deep links grouped by kind, price, expiry warning), **Where to read** (open access first, then preview, borrow, buy). Book sample viewer. Paper abstract + TLDR |
| Streaming | Handle `item`/`done`/`error` events. Keep handling `content` until it's removed |
| Attribution | Render `Availability.attribution` under the links section |

---

## 11. Phase 6: the media agent

### 11.1 What it is
A chat endpoint where each assistant turn is an **`AgentReply`**: an ordered list of blocks (text, media cards, a clarifying question). Behind it is a tool-using loop over the phase 0–5 data layer. The use cases:
- open-ended recommendations ("something like *Arrival* but a book")
- **tip-of-the-tongue identification** ("a 2000s Korean movie where a guy is locked in a hotel room for years")
- follow-ups ("is the second one on Netflix?", "only things under 2 hours")
- the watch/read handoff, either when the user asks or when the agent judges it relevant ("found it, it's on Prime, here's the link")

### 11.2 Endpoint and events
`POST /api/chat`, body `{session_id?: str, message: str, country: str, services?: list[str]}`, returning an NDJSON stream:
```jsonc
{"type": "session", "session_id": "…"}
{"type": "status",  "text": "Searching TMDB for 2000s Korean thrillers…"}   // optional progress, from tool calls
{"type": "block",   "block": { /* hydrated block, see 11.4 */ }}
{"type": "done"}
{"type": "error",   "message": "…"}
```

### 11.3 Loop
A **manual loop** over the Messages API, not the SDK's beta tool runner, because the loop needs per-turn hooks: progress events, tool budgets, and hydration. It runs on `AsyncAnthropic`:

```
while True:
    async with client.messages.stream(model, system, messages, tools, output_config={format: AgentReply schema}) as stream:
        forward text deltas of the structured reply through the block parser (§6.6) → hydrate → emit "block"
    if stop_reason == "tool_use":
        run all tool_use blocks concurrently → append ONE user message with all tool_results
        enforce budgets (max 8 tool calls per user turn, max 3 rounds) → else continue
    elif stop_reason in ("end_turn",): break
    elif stop_reason == "max_tokens": emit error/truncation notice; break
    elif stop_reason == "refusal": emit a safe text block; break
```
Implementation notes:
- **Structured outputs + tools:** `output_config.format` constrains the final reply. Tool-use turns are normal. Run a spike early to confirm streaming partial JSON for the reply works alongside custom tools on the chosen model. The current code already combines `output_config` with the server-side web search tool, which is promising. **Fallback if it doesn't work:** a `reply` tool with `strict: true` whose input schema is `AgentReply`, streamed with `eager_input_streaming: true`. (Validate the parsed input against the schema before use, because eager streaming skips server-side validation.)
- **Parallel tool calls:** return every `tool_result` in a single user message. Splitting them discourages parallel calls on later turns.
- **Errors:** a failed tool returns `tool_result` with `is_error: true` and a short message, so the model can adapt instead of the turn failing.
- **Model choice:** a config setting, not hardcoded. The current app uses `claude-haiku-4-5` (consider switching the config to that undated alias). Whether detective-mode turns need a stronger model is an eval question (§11.9): measure Haiku first, and only escalate per route if the numbers say so. Haiku 4.5 uses `budget_tokens` for extended thinking, while newer models use adaptive thinking and `effort`, so the escalation path needs its own request config.

### 11.4 Reply schema: IDs in, data out
The LLM-facing schema (`anyOf`, per §4.6):
```python
class TextBlock(BaseModel):
    type: Literal["text"]
    text: str                              # markdown

class MediaBlock(BaseModel):
    type: Literal["media"]
    ref: MediaRef                          # must come from this session's tool results
    note: str | None = None                # why it's here / what to look for
    show_availability: bool = False        # agent asks the backend to attach where-to-watch/read
    season: int | None = None              # optional episode-level pointer (shows)
    episode: int | None = None

class QuestionBlock(BaseModel):
    type: Literal["question"]
    question: str
    options: list[str] = []                # rendered as tappable chips; free text is always allowed

AgentBlock = Union[TextBlock, MediaBlock, QuestionBlock]   # anyOf; no discriminator keyword

class AgentReply(BaseModel):
    items: list[AgentBlock]                # "items" so the shared stream parser applies
```

**Hydration** (backend, before emitting each block):
1. `MediaBlock.ref` must appear in the session's **seen-refs set** (every ref returned by any tool this session). Otherwise drop the block and log a `hallucinated_ref` metric. This is the guarantee against made-up titles.
2. Hydrate the item from the cache (it was just fetched by a tool, so this is a cache hit).
3. If `show_availability`, attach `Availability` (watch: the user's `country`; read: region-free), using the cache-first service.
4. Emit `{"type": "block", "block": {"type": "media", "item": {...}, "note": …, "availability": {...}?}}`.

`MediaBlock` is one block type for all four media kinds, because the `ref.type` says which. This keeps the LLM schema small (three variants instead of six) and means a new media kind needs no schema change.

### 11.5 Tools
| Tool | Input | Output (to the model, compact) | Notes |
|---|---|---|---|
| `search_titles` | `{query, media_types[], year_from?, year_to?, country_of_origin?, language?}` | top N `{ref, title, year, type, one-line overview}` | TMDB search/discover + Open Library + S2, chosen by `media_types` |
| `get_details` | `{ref}` | key facts: overview, cast/crew or authors, runtime/pages, genres, keywords | Cache-first. The detective workhorse |
| `find_similar` | `{ref, media_types[]}` | refs + one-liners | TMDB recommendations/similar. Cross-media via search |
| `watch_options_<provider>` | `{ref, country, granularity}` | options summary | **One tool per registered `WatchProvider`**, generated from the registry. Each tool's description is the provider's capability text |
| `read_options` | `{ref}` | options summary | Merged read providers (§7.2) |
| `recall` | `{}` | refs already shown this session with titles | Lets the model resolve "the second one" reliably |

**Provider choice, as requested:** each registered watch provider becomes its own tool, so the **model decides** which to call based on the capabilities in its description ("episode-level links", "iOS/Android app links"). Code adds the plumbing underneath (§7.1): if the chosen provider fails or is over quota, the tool *still* returns the fallback result, labeled with its source. Today the registry has one entry, so the model sees one tool. Adding `WatchmodeProvider` to the list automatically adds `watch_options_watchmode`, with no prompt or loop changes.

Tool results are **compact text**, not raw API JSON. A 6 KB TMDB payload per call would fill the context window fast. Each tool has a `summarize_for_model()` that keeps only what helps the model reason.

### 11.6 Tip-of-the-tongue behavior (prompt + tools, no special code)
The system prompt describes the detective strategy:
1. Turn the description into hypotheses.
2. Search widely (several queries, varied filters).
3. Use `get_details` to confirm or rule out candidates against what the user remembers.
4. When more than one candidate is still plausible, ask **one** `QuestionBlock` with options that best separate them ("Was it animated?", or "Do you remember a twist about his family?").
5. Commit when confident, with a `note` explaining which remembered details matched.

Then offer where to watch or read (`show_availability`) unless the user only wanted the name.

### 11.7 Sessions and context
- A `pikflix.chat_sessions` table (`id`, `created_at`, `messages jsonb`, `seen_refs text[]`, `country`, `services`). It stores the API `messages` array verbatim (including tool turns), so the loop resumes exactly.
- Append-only history. Never rewrite earlier turns, which keeps prompt caching effective. Put `cache_control` on the system prompt and tool definitions, which are stable across all sessions.
- **Context growth:** compact tool results (§11.5) plus a hard cap on stored turns (e.g. the last 20 exchanges, with `seen_refs` persisting beyond the window). Revisit server-side compaction only if real sessions get long.
- Anonymous sessions keyed by an unguessable ID, with a TTL cleanup (7 days). No auth needed for v1.

### 11.8 Guardrails
- Per user turn: max 8 tool calls and 3 loop rounds. Per session: max N metered-provider calls per day (shares the quota guard in §7.1).
- The existing 20 requests/min IP rate limit applies to `/api/chat`. Also cap the message length.
- The model never outputs URLs (the schema has no URL fields). Every link comes from hydration.
- Log per turn: tool calls, rounds, input/output/cache tokens, latency, dropped blocks.

### 11.9 Evals
Build these before tuning prompts, so changes can be measured instead of judged by feel.

| Suite | Source | Metric | Target to ship |
|---|---|---|---|
| **Tip-of-the-tongue** | ~100 *solved* r/tipofmytongue threads for movies/TV (description → known answer). Store as JSONL: `{prompt, answer_ref, turns_allowed}` | Solve rate within 5 turns, with a simulated user (a second model call that answers questions using the thread's details) | Set after the first baseline run, then hold or improve |
| **Golden conversations** | ~30 hand-written multi-turn chats across all four media types | All blocks valid, 0 hallucinated refs, availability attached when asked, `recall` used for "the second one" | 100% structural pass |
| **Search regression** | Fixed queries per shelf | Verified-result count (books), candidate-validity (papers), latency to first item | No regressions from phases 1–2 |

Runs are scripted (`python -m evals.run --suite totl`) and write a JSON report. They call real APIs, so they cost money and quota: run them on demand, not in CI.

---

## 12. Cross-cutting

### 12.1 Platform conventions (in place since `a2d9322`)
- **Services are app-scoped singletons.** Each service has an `@cache`-decorated `get_*_service()` getter used with `Depends`, owns its clients, and has an `async close()`. `lifespan` closes them on shutdown. New services (Open Library, S2, availability providers) follow the same pattern and get added to the `lifespan` close loop.
- **`AsyncAnthropic`** with a shared `httpx.AsyncClient(http2=True)`. Streaming uses `async with client.messages.stream(...)`.
- **One `httpx.AsyncClient` per upstream.** The TMDB client currently uses a single 30s timeout and no HTTP/2. For new clients, and TMDB when it's next touched: set connect 5s / read 15s, and turn on `http2=True` (`h2` is already a dependency).
- The Supabase client is async and created lazily (it can't be built synchronously). `CacheStore` reuses it (§9.1).

### 12.2 Module layout
```
app/
  models.py              # enums, MediaRef, items, Availability (API/DB models)
  schemas.py             # LLM structured-output schemas (anyOf unions only)
  prompts.py             # base + per-media-type injections; agent system prompt
  api/endpoints/
    recommendations.py   # search stream (all shelves)
    availability.py      # /api/availability/{watch,read}
    providers.py         # deprecated after phase 3
    chat.py              # phase 6
  services/
    llm_stream.py        # incremental array-element parser (§6.6)
    anthropic_service.py # AsyncAnthropic wrapper (+ LLMClient protocol, §12.8)
    tmdb_service.py
    openlibrary_service.py
    semantic_scholar_service.py
    cache_store.py       # CacheStore (§9.1)
    supabase_service.py  # shrinks to table-specific helpers on top of CacheStore
    resolvers.py         # Resolver implementations per media type
    availability/
      __init__.py        # AvailabilityService, registries
      watch_streaming_availability.py
      read_openlibrary.py
      read_google_books.py
      read_unpaywall.py
      read_arxiv.py
    agent/               # phase 6
      loop.py
      tools.py
      hydrate.py
      sessions.py
evals/                   # phase 6
```
This stays within `CLAUDE.md`'s rule of "no unnecessary abstractions": each protocol has a concrete reason (several implementations, or a planned second one).

### 12.3 Configuration
- New keys are validated at startup only if their phase is enabled: `FEATURE_BOOKS`, `FEATURE_PAPERS`, `FEATURE_WATCH_LINKS`, `FEATURE_READ_LINKS`, `FEATURE_AGENT`. Default off until each phase ships, so partially built phases can merge to `develop` safely.
- `CACHE_TTL`, quotas, and the agent budgets in §11.8 live in `config.py` as constants.

### 12.4 Errors and degradation
| Failure | Behavior |
|---|---|
| A resolver can't verify a pick | Drop it, log it, continue (counts toward `done.dropped`) |
| An upstream (TMDB/OL/S2) is down | That pick fails; others continue. If the LLM call itself fails, emit `error` and end |
| A watch provider fails or is over quota | Fall back (§7.1). The UI shows TMDB provider names without deep links, plus the attribution |
| A read provider fails | Omit its options. The others still render |
| The agent exceeds its tool budget | Force a final reply using what it has (a system message: "answer now with what you have") |

### 12.5 Testing
- **Unit:** title verification (§6.3 thresholds, with collision fixtures), trailer selection, option mapping per provider, the stream parser (escaped quotes, nested blocks, split chunks, invalid elements), schema snapshots (§4.6).
- **Contract:** recorded upstream fixtures (TMDB, OL, S2, Streaming Availability, Unpaywall, Google Books) replayed through `httpx.MockTransport`. No live calls in tests.
- **Integration:** one smoke test per shelf against a local FastAPI app with mocked upstreams and a stubbed LLM stream.
- `mypy` (already a pre-commit hook) on all new modules.

### 12.6 Observability
Structured log line per search and per agent turn: shelf/media types, picks, verified, dropped, cache hits/misses per table, upstream latencies, LLM tokens (including cache reads), total latency. That's enough to watch the free-tier quotas and cache effectiveness without new infrastructure.

### 12.7 Performance budget
- Time to first card: ≤ today's baseline + 10% for watch, ≤ 3s p50 for books, ≤ 5s p50 for papers (they include retrieval before ranking).
- Hydration and resolution per pick run concurrently (fixes P7, **Phase 0**). Today the endpoint `await`s each pick's cache lookup and TMDB fetch inside the `async for` over the LLM stream, so later picks wait for earlier ones. Instead, start an `asyncio` task per pick as it arrives, and have the generator yield results from a queue in completion order. Picks don't depend on each other, and the client renders in arrival order, so no reorder window is needed. Cancel outstanding tasks if the client disconnects.

### 12.8 LLM provider abstraction
A thin `LLMClient` protocol with two operations is enough to add a second provider later without touching pipelines:
- `stream_structured(system, messages, schema) -> AsyncIterator[str]` (JSON text deltas)
- `run_turn(system, messages, tools, schema) -> TurnResult`

Only `AnthropicClient` is implemented. Schema dialects and tool formats differ between providers, so translation belongs inside each client.

### 12.9 Dependencies
- No new heavy dependencies before the optional §8.3 PDF thumbnails.
- `anthropic==0.87.0` is pinned. It was bumped from 0.84.0 because `transform_schema` before 0.87 moved `enum` into the description text instead of keeping it as a constraint, so `content_type` wasn't actually limited to movie/show. The schema test now asserts the enum survives. Before phase 6, check the SDK changelog for structured-output and streaming changes. The 1.x upgrade should be its own PR, separate from this work.

---

## 13. Risks and open questions

| # | Risk / question | Mitigation / decision needed |
|---|---|---|
| R1 | Streaming Availability's free tier (100/day) runs out under real traffic | On-demand fetching + 24h cache + quota guard + TMDB fallback. Decide on a paid tier if daily usage regularly passes ~70% |
| R2 | Book verification is too strict (drops good picks) or too loose (wrong edition or author) | Log drop reasons. Tune thresholds against a fixture set of ~50 tricky titles |
| R3 | Semantic Scholar's anonymous rate limits are unreliable | Get an API key before phase 2 ships. OpenAlex is the fallback retriever |
| R4 | Structured outputs + custom tools + streaming may not interact as expected on the chosen model | A phase-6 spike in week 1. Fallback design in §11.3 |
| R5 | Tip-of-the-tongue quality on Haiku | Measure with the eval. Escalate model per route only if the numbers justify it |
| Q1 | Deep-link provider terms: is attribution required on Streaming Availability's free tier? Is commercial use allowed if PikFlix ever monetizes? | Read the terms before phase 3 ships |
| Q2 | Should Read get its own toggle "Both" (books + papers mixed), or always one type at a time? Papers and books need different pipelines, so "Both" means two parallel pipelines merged by the ranker | Proposal: allow it, with two pipelines and interleaved results |
| Q3 | Country detection for availability: browser locale, IP, or an explicit picker? | Proposal: browser locale default plus a picker in the details panel, stored in localStorage |
| Q4 | Should the "why this pick" `reason` be shown? Today it's intentionally not sent (`Movie`/`Show` have no `reason` field). Only worth sending with a good place to show it | Ideas: a one-line subtext under the card title on hover, or a "Why this?" line in the details panel. Decide before phase 1, which moves `reason` into `SearchResultItem` (§4.4) |

---

## 14. Phase checklists

**Phase 0: Trailers + stream fixes**
- [x] `AsyncAnthropic` singleton, shared `httpx.AsyncClient`s, async Supabase, provider TTL, escape handling (`a2d9322`)
- [x] `llm_stream.py` parser with nesting support (escape logic moved over), plus tests; switch the watch pipeline to it
- [x] Rename the schema wrapper key `movies` → `items`, plus a schema snapshot test
- [x] Concurrent per-pick resolution and hydration (P7)
- [x] `append_to_response=videos`, `pick_trailer`, migration for `trailer`/`videos_checked_at`, lazy backfill
- [ ] Apply the trailer migration (`supabase db push`) **before** deploying the backend: otherwise cache saves fail on the unknown columns
- [x] Client: trailer badge on cards, click-to-play trailer in the details panel
- [x] mypy clean across `app/` (TMDB fallback lookup, provider endpoint return types)

**Phase 0 (frontend polish)**
- [x] Animate cards in as they stream: each card fades and rises in (420ms). New cards book the next slot on a timeline at least 90ms after the previous one, so a burst of 4 flows in over ~270ms instead of popping in together. Delays are fixed per card id, so re-renders never replay them. Respects `prefers-reduced-motion`
- [x] Search bar jump at the end of the first-search animation. Two causes, both verified by measuring the bar's rect in headless Chrome at 700–1300px heights and 390–1920px widths (now pixel-identical in position and size):
  - Vertical position: the animating bar is `absolute` and ended at `top: 7.9%` of its 90vh container (≈7.1% of the viewport), while the bar that replaces it is `fixed top-16` (always 64px). They only matched on a ~900px tall window (measured: 7px jump at 800px, 28px at 1300px). Fix: end the keyframe at `top: 4rem`. The identical `moveToTop`/`moveToTopMobile` keyframes were merged
  - Height (2px): `focus:border-none` removed the 1px borders while focused, and the auto-resize set `height = scrollHeight`, which excludes borders. Either way the typed-in bar was 56px and the fresh fixed bar 58px. Fix: `focus:border-transparent` (keeps the border width) and a border-aware resize. This also removes the 2px shrink when focusing the search bar
- [ ] Trailer playing cuts off the poster. The poster overlaps the hero by half its height (`translate-y-1/2`), and the iframe covers the hero, including the poster's top half. Options:
  - (a) **Slide the poster and content down** while the trailer plays, so the poster sits fully below the video. Animate the poster from `translate-y-1/2` to `translate-y-full` (plus a gap) and the content's top padding to match. Keeps everything visible; costs one extra animated layout shift. *(Recommended.)*
  - (b) Raise the poster above the video. Simple, but it covers the player's bottom-left corner (progress bar, play/pause).
  - (c) Fade the poster out while playing, and back in when the trailer closes. Clean, but loses the poster exactly when the user is most engaged.
  - (d) Shrink the poster into a small thumbnail next to the title while playing.
- [ ] Close the trailer without closing the panel. Today the only X closes the whole panel. Add a "Close trailer" control (e.g. a small ✕ or "Back to details" at the video's top-left, so it doesn't sit next to the panel's X) that swaps the backdrop back in. `Esc` could close the trailer first, then the panel on a second press
- [ ] Loading state after clicking Play: the YouTube player takes a moment, showing a black box. Keep the backdrop and a spinner on the button until the iframe's `onLoad` fires, then fade the video in
- Not needed: async/background trailer fetching. The trailer comes from the same TMDB details request as the rest of the card (or from the cache row), so it can't be slow or fail on its own: the card arrives with its trailer chosen, or with `trailer: null` and no play button. A video removed from YouTube after caching shows YouTube's own "unavailable" message, and the 7-day refresh replaces the key

**Phase 1: Model + Books**
- [ ] `MediaType`, `Shelf`, `MediaRef`, `Book`, `SearchResultItem`. `reason` moved off items
- [ ] `CacheStore` on the existing async client. ID-first watch caching (P6), and correct the cache-matching line in `CLAUDE.md`
- [ ] `error`/`done` events instead of swallowing LLM errors (P8)
- [ ] Migration: rename `content_type` → `media_type` enum, add `book`/`paper` values
- [ ] `OpenLibraryResolver` with verification. `books` table
- [ ] NDJSON v2 (`item`, `done`) alongside legacy `content`
- [ ] Client: `Book` type, card, details

**Phase 2: Papers**
- [ ] S2 client (API key), retrieve-then-rank pipeline, candidate validation, `papers` table
- [ ] Client: `Paper` type, card, details (abstract, TLDR)

**Phase 3: Watch links**
- [ ] `WatchProvider` registry, `StreamingAvailabilityProvider`, `AvailabilityService` with fallback + quota guard
- [ ] `availability` + `provider_usage` tables
- [ ] `/api/availability/watch`. Client "Where to watch" with deep links + attribution

**Phase 4: Read links**
- [ ] Read providers (Open Library, Google Books, Unpaywall, arXiv, DOI), merge + dedupe
- [ ] `/api/availability/read`. Client "Where to read", book sample viewer

**Phase 5: Tabs**
- [ ] `media_types` request param (+ legacy mapping), per-media prompts
- [ ] Client: Watch/Read tabs, per-tab toggles, URL state, per-tab history

**Phase 6: Agent**
- [ ] Spike: structured reply + custom tools + streaming on the chosen model (R4)
- [ ] Tools (registry-generated watch tools), loop, budgets, hydration with seen-refs check
- [ ] Sessions table, `/api/chat`, NDJSON events
- [ ] Eval suites + baseline. Client chat UI with text/media/question blocks
