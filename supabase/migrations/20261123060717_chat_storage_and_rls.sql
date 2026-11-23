-- Agent chat storage, and row-level security on every pikflix table.
--
-- The browser will soon hold the Supabase anon key (anonymous sign-in needs it). With RLS on and no
-- policies, that key can't read or write any table here: only the backend's service-role key (which
-- bypasses RLS) can. The backend enforces chat ownership itself.
--
-- Apply only AFTER the backend's SUPABASE_KEY is the service-role key, or the API loses table access.

CREATE TABLE pikflix.chat_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title text NOT NULL,
  seen_refs text[] NOT NULL DEFAULT '{}',   -- every ref any tool returned; blocks may only cite these
  created_at timestamptz NOT NULL DEFAULT now(),
  last_active_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chat_sessions_owner_recent_idx ON pikflix.chat_sessions (owner_id, last_active_at DESC);

-- Append-only: one row per Messages API message, content blocks stored verbatim so a turn replays exactly
CREATE TABLE pikflix.chat_messages (
  session_id uuid NOT NULL REFERENCES pikflix.chat_sessions(id) ON DELETE CASCADE,
  seq integer NOT NULL,
  role text NOT NULL CHECK (role IN ('user', 'assistant')),
  content jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, seq)
);

ALTER TABLE pikflix.movies        ENABLE ROW LEVEL SECURITY;
ALTER TABLE pikflix.shows         ENABLE ROW LEVEL SECURITY;
ALTER TABLE pikflix.providers     ENABLE ROW LEVEL SECURITY;
ALTER TABLE pikflix.books         ENABLE ROW LEVEL SECURITY;
ALTER TABLE pikflix.chat_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE pikflix.chat_messages ENABLE ROW LEVEL SECURITY;

-- Retention: chats of users who are still anonymous expire after max_age without activity. Signed-up
-- users keep theirs (upgrading clears is_anonymous, so their chats simply stop qualifying).
-- SECURITY DEFINER because it reads auth.users; only the service role may call it.
CREATE FUNCTION pikflix.delete_expired_anonymous_chats(max_age interval DEFAULT interval '30 days')
RETURNS integer
LANGUAGE sql
SECURITY DEFINER
SET search_path = ''
AS $$
  WITH deleted AS (
    DELETE FROM pikflix.chat_sessions s
    USING auth.users u
    WHERE s.owner_id = u.id
      AND u.is_anonymous
      AND s.last_active_at < now() - max_age
    RETURNING 1
  )
  SELECT count(*)::integer FROM deleted;
$$;
REVOKE ALL ON FUNCTION pikflix.delete_expired_anonymous_chats(interval) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION pikflix.delete_expired_anonymous_chats(interval) TO service_role;
