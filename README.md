# PikFlix API Server

A FastAPI server that powers the PikFlix natural language movie discovery platform. This server provides intelligent movie recommendations based on user queries in natural language.

## Features

- Natural language processing for movie recommendation queries
- Integration with Anthropic Claude API for intelligent query understanding
- TMDB API integration for comprehensive movie data
- Supabase database caching for faster responses and reduced API calls
- Modular architecture for maintainability and extensibility

## Installation

### Prerequisites

- Python 3.11 or higher
- Supabase account with database setup
- TMDB API key
- Anthropic API key

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/pikflix-api.git
   cd pikflix-api
   ```

2. Create a virtual environment and activate it:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Create a `.env` file with your API keys:
   ```
   ANTHROPIC_API_KEY=your_anthropic_api_key
   TMDB_API_KEY=your_tmdb_api_key
   SUPABASE_URL=your_supabase_url
   SUPABASE_KEY=your_supabase_key
   ```

5. Set up the Supabase database schema by running the SQL commands provided in the Database Setup section below.

## Usage

### Running the server

```bash
uvicorn app.main:app --reload
```

Or using the Python script:

```bash
python -m app.main
```

The server will be available at `http://localhost:8000`.

### API Endpoints

#### Health Check
```
GET /health
```
Returns a simple status message to verify the server is running.

#### Movie Recommendations
```
POST /api/movies/recommendations
```
Body:
```json
{
  "query": "A suspenseful thriller with unexpected twists and a female protagonist"
}
```
Returns a list of movie recommendations matching the query.

## Database Setup

Run the following SQL commands in your Supabase SQL editor to set up the required schema and tables:

```sql
-- Create the pikflix schema
CREATE SCHEMA IF NOT EXISTS pikflix;

-- Create the movies table with all TMDB fields
CREATE TABLE IF NOT EXISTS pikflix.movies (
    id INTEGER PRIMARY KEY,
    imdb_id TEXT,
    title TEXT NOT NULL,
    original_title TEXT,
    original_language TEXT,
    overview TEXT,
    tagline TEXT,
    status TEXT,
    release_date DATE,
    adult BOOLEAN,
    budget BIGINT,
    revenue BIGINT,
    runtime INTEGER,
    vote_average FLOAT,
    vote_count INTEGER,
    popularity FLOAT,
    video BOOLEAN,
    poster_path TEXT,
    backdrop_path TEXT,
    homepage TEXT,
    belongs_to_collection JSONB,
    genres JSONB,
    production_companies JSONB,
    production_countries JSONB,
    spoken_languages JSONB,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS movies_title_idx ON pikflix.movies (title);
CREATE INDEX IF NOT EXISTS movies_last_updated_idx ON pikflix.movies (last_updated);
```

## Project Structure

```
pikflix-api/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI application entry point
│   ├── config.py           # Configuration and environment variables
│   ├── models.py           # Pydantic models
│   ├── services/           # Service modules
│   │   ├── __init__.py
│   │   ├── anthropic_service.py   # Anthropic API integration
│   │   ├── supabase_service.py    # Supabase database operations
│   │   ├── tmdb_service.py        # TMDB API integration
│   └── api/                # API routes
│       ├── __init__.py
│       └── endpoints/
│           ├── __init__.py
│           └── movies.py   # Movie recommendation endpoints
├── requirements.txt        # Python dependencies
└── .env                    # Environment variables (not in git)
```

## Environment Variables

The following environment variables are required:

- `ANTHROPIC_API_KEY`: Your Anthropic API key for Claude
- `TMDB_API_KEY`: Your TMDB API key
- `SUPABASE_URL`: Your Supabase project URL
- `SUPABASE_KEY`: Your Supabase service role key

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.