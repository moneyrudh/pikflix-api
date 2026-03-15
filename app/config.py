import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings:
    """Validates required environment variables at startup and provides typed access."""

    def __init__(self):
        self.ANTHROPIC_API_KEY = self._require("ANTHROPIC_API_KEY")
        self.TMDB_READ_ACCESS_TOKEN = self._require("TMDB_READ_ACCESS_TOKEN")
        self.SUPABASE_URL = self._require("SUPABASE_URL")
        self.SUPABASE_KEY = self._require("SUPABASE_KEY")

        # Optional API key for protecting endpoints (strongly recommended in production)
        self.API_KEY = os.getenv("PIKFLIX_API_KEY")

        # CORS origins — comma-separated list, defaults to wildcard for development
        origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
        self.allowed_origins = [o.strip() for o in origins_raw.split(",")]

        # Cache settings (in hours)
        self.CACHE_DURATION = 24 * 7  # 1 week default cache

        # Anthropic API settings
        self.ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"

        # API Base URLs
        self.TMDB_BASE_URL = "https://api.themoviedb.org/3"

    @staticmethod
    def _require(name: str) -> str:
        value = os.getenv(name)
        if not value:
            print(f"FATAL: Required environment variable '{name}' is not set. Exiting.")
            sys.exit(1)
        return value


# Singleton — validates on first import, fails fast if config is missing
settings = Settings()
