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

# Anthropic API settings
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# CORS
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
]

# API Base URLs
TMDB_BASE_URL = "https://api.themoviedb.org/3"
