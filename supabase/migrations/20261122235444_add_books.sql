-- Books, cached at the work level (all editions together) under their Open Library work key.
CREATE TABLE pikflix.books (
  id text PRIMARY KEY,                    -- Open Library work key, e.g. 'OL893414W'
  title text NOT NULL,
  subtitle text,
  authors jsonb NOT NULL DEFAULT '[]',    -- [{name, id}]
  first_publish_year integer,
  description text,                       -- fetched when the details panel opens
  description_checked_at timestamptz,     -- set once fetched, even when Open Library has none
  cover_id integer,
  isbns jsonb NOT NULL DEFAULT '[]',
  subjects jsonb NOT NULL DEFAULT '[]',
  page_count integer,
  edition_count integer,
  ia_ids jsonb NOT NULL DEFAULT '[]',
  ebook_access text,
  lookup_key text NOT NULL,               -- normalized title + author surname, so repeat picks skip Open Library
  last_updated timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX books_lookup_key_idx ON pikflix.books (lookup_key);
