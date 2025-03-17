import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Keys and configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Cache settings (in hours)
CACHE_DURATION = 24 * 7  # 1 week default cache

# Anthropic API settings
ANTHROPIC_MODEL = "claude-3-opus-20240229"

# API Base URLs
TMDB_BASE_URL = "https://api.themoviedb.org/3"