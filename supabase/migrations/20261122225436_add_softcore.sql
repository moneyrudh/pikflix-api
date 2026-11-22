-- TMDB added a `softcore` flag to movie and TV details. Saving a details payload with a
-- field the table lacks fails the whole upsert, so new titles weren't being cached.
ALTER TABLE pikflix.movies ADD COLUMN softcore boolean NULL;
ALTER TABLE pikflix.shows  ADD COLUMN softcore boolean NULL;
