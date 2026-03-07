-- Baseline migration: existing remote schema as of 2026-03-07

CREATE SCHEMA IF NOT EXISTS pikflix;

CREATE TABLE pikflix.movies (
  id integer NOT NULL,
  imdb_id text NULL,
  title text NOT NULL,
  original_title text NULL,
  original_language text NULL,
  overview text NULL,
  tagline text NULL,
  status text NULL,
  release_date date NULL,
  adult boolean NULL,
  budget bigint NULL,
  revenue bigint NULL,
  runtime integer NULL,
  vote_average double precision NULL,
  vote_count integer NULL,
  popularity double precision NULL,
  video boolean NULL,
  poster_path text NULL,
  backdrop_path text NULL,
  homepage text NULL,
  belongs_to_collection jsonb NULL,
  genres jsonb NULL,
  production_companies jsonb NULL,
  production_countries jsonb NULL,
  spoken_languages jsonb NULL,
  last_updated timestamp with time zone NULL DEFAULT CURRENT_TIMESTAMP,
  origin_country text NULL,
  CONSTRAINT movies_pkey PRIMARY KEY (id)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS movies_last_updated_idx ON pikflix.movies USING btree (last_updated) TABLESPACE pg_default;
CREATE INDEX IF NOT EXISTS movies_title_idx ON pikflix.movies USING btree (title) TABLESPACE pg_default;

CREATE TABLE pikflix.providers (
  movie_id integer NULL,
  last_updated timestamp with time zone NULL DEFAULT CURRENT_TIMESTAMP,
  results jsonb NULL,
  CONSTRAINT providers_movie_id_key UNIQUE (movie_id),
  CONSTRAINT providers_movie_id_fkey FOREIGN KEY (movie_id) REFERENCES pikflix.movies (id)
) TABLESPACE pg_default;
