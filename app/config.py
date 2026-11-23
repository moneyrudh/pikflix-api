import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Keys and configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_READ_ACCESS_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
# Optional. Open Library gives identified clients (app name + contact in the User-Agent) 3 req/s instead of 1.
OPENLIBRARY_CONTACT = os.getenv("OPENLIBRARY_CONTACT")
# Optional but needed in production: without it, Semantic Scholar's anonymous tier is shared by everyone and throttled
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY")

# Validate required env vars
_required = {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "TMDB_READ_ACCESS_TOKEN": TMDB_READ_ACCESS_TOKEN,
    "SUPABASE_URL": SUPABASE_URL,
    "SUPABASE_KEY": SUPABASE_KEY,
}
_missing = [name for name, val in _required.items() if not val]
if _missing:
    print(f"Missing required environment variables: {', '.join(_missing)}", file=sys.stderr)
    sys.exit(1)

# Cache settings (in hours)
CACHE_DURATION = 24 * 7  # 1 week default cache
PROVIDER_CACHE_DURATION = 24  # streaming availability changes often
BOOK_CACHE_DURATION = 24 * 30  # book metadata is effectively static

# Books: ask Claude for extra picks so ones that fail verification don't leave the user short
BOOK_PICKS_REQUESTED = 12
BOOK_RESULTS_SHOWN = 9

# Papers: Claude plans keyword queries, Semantic Scholar returns real candidates, Claude ranks them
PAPER_QUERIES_MAX = 3
PAPER_RESULTS_PER_QUERY = 20
PAPER_CANDIDATES_MAX = 40
PAPER_RESULTS_SHOWN = 9

# Anthropic API settings
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# CORS
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
]

# API Base URLs
TMDB_BASE_URL = "https://api.themoviedb.org/3"
OPENLIBRARY_BASE_URL = "https://openlibrary.org"
OPENLIBRARY_USER_AGENT = "PikFlix/1.0 (https://pikflix.app" + (f"; {OPENLIBRARY_CONTACT})" if OPENLIBRARY_CONTACT else ")")
# Open Library's published limits; shared by every request this process makes
OPENLIBRARY_REQUESTS_PER_SECOND = 3 if OPENLIBRARY_CONTACT else 1
SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1"
# The introductory API-key limit; also polite on the shared anonymous tier
SEMANTIC_SCHOLAR_REQUESTS_PER_SECOND = 1
