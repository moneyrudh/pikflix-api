-- 0. Create content_type enum
CREATE TYPE pikflix.content_type AS ENUM ('movie', 'show');

-- 1. Create shows table
CREATE TABLE pikflix.shows (
  id integer NOT NULL,
  name text NOT NULL,
  original_name text NULL,
  original_language text NULL,
  overview text NULL,
  tagline text NULL,
  status text NULL,
  first_air_date date NULL,
  last_air_date date NULL,
  number_of_seasons integer NULL,
  number_of_episodes integer NULL,
  episode_run_time jsonb NULL,
  adult boolean NULL,
  vote_average double precision NULL,
  vote_count integer NULL,
  popularity double precision NULL,
  poster_path text NULL,
  backdrop_path text NULL,
  homepage text NULL,
  genres jsonb NULL,
  production_companies jsonb NULL,
  production_countries jsonb NULL,
  spoken_languages jsonb NULL,
  networks jsonb NULL,
  created_by jsonb NULL,
  origin_country text NULL,
  in_production boolean NULL,
  type text NULL,
  last_updated timestamp with time zone NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT shows_pkey PRIMARY KEY (id)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS shows_last_updated_idx ON pikflix.shows USING btree (last_updated) TABLESPACE pg_default;
CREATE INDEX IF NOT EXISTS shows_name_idx ON pikflix.shows USING btree (name) TABLESPACE pg_default;

-- 2. Generalize providers table to support both movies and shows
ALTER TABLE pikflix.providers DROP CONSTRAINT providers_movie_id_fkey;
ALTER TABLE pikflix.providers DROP CONSTRAINT providers_movie_id_key;
ALTER TABLE pikflix.providers RENAME COLUMN movie_id TO content_id;
ALTER TABLE pikflix.providers ADD COLUMN content_type pikflix.content_type NOT NULL DEFAULT 'movie';
ALTER TABLE pikflix.providers ADD CONSTRAINT providers_content_key UNIQUE (content_id, content_type);
