import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Keys and configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_READ_ACCESS_TOKEN = os.getenv("TMDB_READ_ACCESS_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Cache settings (in hours)
CACHE_DURATION = 24 * 7  # 1 week default cache

# Anthropic API settings
ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"

# CORS
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
]

# API Base URLs
TMDB_BASE_URL = "https://api.themoviedb.org/3"
