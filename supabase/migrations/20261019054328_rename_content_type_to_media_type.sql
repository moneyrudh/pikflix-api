-- One enum for every media type, named like the Python `MediaType`.
-- RENAME is metadata-only: providers.content_type keeps working, now typed pikflix.media_type.
ALTER TYPE pikflix.content_type RENAME TO media_type;

-- Values added with ADD VALUE can't be used in the same transaction, so nothing here inserts them.
ALTER TYPE pikflix.media_type ADD VALUE IF NOT EXISTS 'book';
ALTER TYPE pikflix.media_type ADD VALUE IF NOT EXISTS 'paper';
