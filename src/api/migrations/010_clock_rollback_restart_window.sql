-- Additive restart-window safety correction: preserve future crash timestamps
-- across backwards wall-clock jumps. Migration 008 is immutable.
--
-- Restart authority is fail-closed: if the system clock jumps backwards after
-- three admitted crash restarts, their timestamps remain counted instead of
-- being silently discarded by the former upper bound (now() + interval '1 hour').
-- The owner can inspect/reset a genuine clock-recovery event explicitly;
-- ordinary clean-stop starts are still free and all crash limits remain fixed.
--
-- This migration is deliberately data-preserving: no counter edits, resets,
-- role grants, or changes to release/installation identity.
DO $migration$
BEGIN
 IF NOT EXISTS(
    SELECT 1 FROM pg_class
    WHERE oid='tester_platform.restart_budgets'::regclass
      AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
      AND relkind='r'
 ) THEN
    RAISE EXCEPTION 'restart budget owner invalid';
 END IF;
END $migration$;

CREATE OR REPLACE FUNCTION tester_platform.restart_window_recent(p_times timestamptz[])
RETURNS timestamptz[] LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
    SELECT coalesce(array_agg(t ORDER BY t), '{}'::timestamptz[])
    FROM unnest(p_times) AS t
    WHERE t > now() - interval '1 hour'
$$;

-- Keep the SQL helper owner-only even if an older migration did not.
REVOKE ALL ON FUNCTION tester_platform.restart_window_recent(timestamptz[]) FROM PUBLIC;
