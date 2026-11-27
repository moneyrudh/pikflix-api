-- Server-side usage limits for anything that costs Claude tokens: agent turns per user, searches per
-- visitor. One row per counted request, keyed by bucket ('chat', 'search') and a key (a user id, or a
-- hashed IP; raw IPs are never stored). Rows are only needed for a day, and each call clears the
-- caller's older ones. Kept apart from the chats themselves, so deleting a chat doesn't reset a count.
CREATE TABLE pikflix.rate_limit_hits (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bucket     text NOT NULL,
    key        text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rate_limit_hits_lookup_idx ON pikflix.rate_limit_hits (bucket, key, created_at DESC);
ALTER TABLE pikflix.rate_limit_hits ENABLE ROW LEVEL SECURITY;

-- Check a caller's per-minute and per-day limits and, if allowed, record the request, in one call.
-- Returns whether it may go ahead and, if not, how many seconds until it could. The row lock on the
-- caller's key serializes concurrent calls, so a burst can't slip past the limit. Service role only.
CREATE FUNCTION pikflix.take_rate_limit(p_bucket text, p_key text, p_per_minute integer, p_per_day integer)
RETURNS TABLE (allowed boolean, retry_after integer)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  minute_count integer;
  day_count    integer;
  oldest       timestamptz;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext(p_bucket || ':' || p_key));

  DELETE FROM pikflix.rate_limit_hits h
   WHERE h.bucket = p_bucket AND h.key = p_key AND h.created_at < now() - interval '1 day';

  SELECT count(*) FILTER (WHERE h.created_at > now() - interval '1 minute'), count(*)
    INTO minute_count, day_count
    FROM pikflix.rate_limit_hits h
   WHERE h.bucket = p_bucket AND h.key = p_key;

  IF day_count >= p_per_day THEN
    SELECT min(h.created_at) INTO oldest
      FROM pikflix.rate_limit_hits h WHERE h.bucket = p_bucket AND h.key = p_key;
    RETURN QUERY SELECT false, greatest(1, ceil(extract(epoch FROM oldest + interval '1 day' - now())))::integer;
    RETURN;
  END IF;

  IF minute_count >= p_per_minute THEN
    SELECT min(h.created_at) INTO oldest
      FROM pikflix.rate_limit_hits h
     WHERE h.bucket = p_bucket AND h.key = p_key AND h.created_at > now() - interval '1 minute';
    RETURN QUERY SELECT false, greatest(1, ceil(extract(epoch FROM oldest + interval '1 minute' - now())))::integer;
    RETURN;
  END IF;

  INSERT INTO pikflix.rate_limit_hits (bucket, key) VALUES (p_bucket, p_key);
  RETURN QUERY SELECT true, 0;
END;
$$;
REVOKE ALL ON FUNCTION pikflix.take_rate_limit(text, text, integer, integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION pikflix.take_rate_limit(text, text, integer, integer) TO service_role;
