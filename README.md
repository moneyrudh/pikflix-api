# PikFlix API

FastAPI backend for PikFlix — a natural language movie and TV show recommendation app.

User describes what they want to watch → Claude recommends content → TMDB provides metadata → Supabase caches it → NDJSON stream back to the frontend.

## Stack

- **FastAPI** / Python 3.11
- **Claude Haiku 4.5** for recommendations (streaming structured output)
- **TMDB API** for movie/show metadata and watch providers
- **Supabase** for caching (1 week TTL)
- Deployed on **Railway** via Docker

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create .env
ANTHROPIC_API_KEY=...
TMDB_API_KEY=...
TMDB_READ_ACCESS_TOKEN=...
SUPABASE_URL=...
SUPABASE_KEY=...
CORS_ORIGINS=http://localhost:3000

# Run
uvicorn app.main:app --reload
```

### Docker
```bash
docker build -t pikflix-api .
docker run -p 8000:8000 --env-file .env pikflix-api
```

## Project Structure

```
pikflix-api/
├── app/
│   ├── main.py                # FastAPI app, CORS, logging
│   ├── config.py              # Env vars and constants
│   ├── models.py              # Pydantic models, enums (ContentType, ContentTypeMode)
│   ├── schemas.py             # Claude structured output schemas
│   ├── prompts.py             # System prompts (base + content-type injections)
│   ├── api/endpoints/
│   │   ├── recommendations.py # /api/recommendations/ streaming endpoint
│   │   └── providers.py       # /api/providers/ watch providers
│   └── services/
│       ├── anthropic_service.py # Claude streaming + structured output parsing
│       ├── tmdb_service.py      # TMDB API client (movies + shows + fallback)
│       └── supabase_service.py  # Supabase caching (movies + shows tables)
├── supabase/
│   └── migrations/            # SQL migrations (Supabase CLI)
├── requirements.txt
└── .env                       # Environment variables (not in git)
```

## License

MIT
