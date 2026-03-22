-- Add missing columns that TMDB /tv/{id} returns
ALTER TABLE pikflix.shows ADD COLUMN IF NOT EXISTS languages jsonb NULL;
ALTER TABLE pikflix.shows ADD COLUMN IF NOT EXISTS seasons jsonb NULL;
ALTER TABLE pikflix.shows ADD COLUMN IF NOT EXISTS last_episode_to_air jsonb NULL;
ALTER TABLE pikflix.shows ADD COLUMN IF NOT EXISTS next_episode_to_air jsonb NULL;

-- Fix origin_country type: TMDB returns an array, not a single string
ALTER TABLE pikflix.shows ALTER COLUMN origin_country TYPE jsonb USING to_jsonb(ARRAY[origin_country]) ;
