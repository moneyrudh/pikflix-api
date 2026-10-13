-- Trailer picked from TMDB /videos (YouTube key + metadata), and when videos were last checked.
-- Rows with videos_checked_at IS NULL are refetched once on their next cache hit (lazy backfill).
ALTER TABLE pikflix.movies ADD COLUMN IF NOT EXISTS trailer jsonb NULL;
ALTER TABLE pikflix.movies ADD COLUMN IF NOT EXISTS videos_checked_at timestamp with time zone NULL;
ALTER TABLE pikflix.shows ADD COLUMN IF NOT EXISTS trailer jsonb NULL;
ALTER TABLE pikflix.shows ADD COLUMN IF NOT EXISTS videos_checked_at timestamp with time zone NULL;
