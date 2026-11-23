-- Papers, cached under their Semantic Scholar paperId, so the agent can show (and a reopened chat can
-- re-show) a paper by ref without re-running the search that found it. Paper searches save their
-- candidates here. RLS on with no policies, like every pikflix table: backend (secret key) only.
CREATE TABLE pikflix.papers (
  id text PRIMARY KEY,                   -- Semantic Scholar paperId
  title text NOT NULL,
  authors jsonb NOT NULL DEFAULT '[]',   -- [{name, id}]
  year integer,
  publication_date date,
  venue text,
  abstract text,
  tldr text,
  doi text,
  arxiv_id text,
  citation_count integer,
  fields_of_study jsonb NOT NULL DEFAULT '[]',
  open_access_pdf text,
  last_updated timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE pikflix.papers ENABLE ROW LEVEL SECURITY;
