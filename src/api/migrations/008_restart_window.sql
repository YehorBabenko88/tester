-- Crash restarts are bounded inside a sliding window; clean starts are not counted.
-- Migration 003 stays byte-identical: its lifetime *_starts columns are retained
-- only as legacy evidence and are no longer incremented. The same two runtime
-- function signatures are replaced in place, so existing EXECUTE grants (and the
-- absence of any table privilege for the App role) carry over unchanged. Crash
-- history lives in the owner-protected restart_budgets row itself: no new table
-- or sequence is created that a schema-wide runtime GRANT could expose.
DO $migration$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid='tester_platform.restart_budgets'::regclass
               AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
               AND relkind='r')
 THEN RAISE EXCEPTION 'restart budget owner invalid'; END IF;
END $migration$;

ALTER TABLE tester_platform.restart_budgets
    ADD COLUMN host_run_digest text
        CHECK (host_run_digest IS NULL OR host_run_digest ~ '^[0-9a-f]{64}$'),
    ADD COLUMN host_run_started_at timestamptz,
    ADD COLUMN host_crashes timestamptz[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(host_crashes) <= 3 AND array_position(host_crashes, NULL) IS NULL),
    ADD COLUMN api_crashes timestamptz[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(api_crashes) <= 6 AND array_position(api_crashes, NULL) IS NULL),
    ADD COLUMN worker_crashes timestamptz[] NOT NULL DEFAULT '{}'
        CHECK (cardinality(worker_crashes) <= 6 AND array_position(worker_crashes, NULL) IS NULL);

-- Fixed window/limits. Entries further than one window in the future (wall clock
-- moved backwards) are discarded instead of blocking forever.
CREATE FUNCTION tester_platform.restart_window_recent(p_times timestamptz[])
RETURNS timestamptz[] LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
    SELECT coalesce(array_agg(t ORDER BY t), '{}'::timestamptz[])
    FROM unnest(p_times) AS t
    WHERE t > now() - interval '1 hour' AND t <= now() + interval '1 hour'
$$;

-- p_role grammar (all other values are denied):
--   host:<32 hex>        service-host start; crash restart iff the previous host run
--                        never recorded a clean stop (crash/kill/power loss)
--   host-stop:<32 hex>   requested (operator/SCM/OS shutdown) stop of that exact run;
--                        the only kind still admitted while the owner kill switch is set
--                        (it ends a run, never starts anything)
--   api-start|worker-start  initial child launch of a supervisor run (not counted)
--   api|worker           child crash restart (counted)
--   host                 legacy untokenized start of pre-008 code (e.g. an old app
--                        restored on an upgraded schema). It can never record a clean
--                        stop, so every legacy start is conservatively counted as a host
--                        crash restart inside the same window (never unbounded), and it
--                        leaves a run marker no token matches, so the next tokenized
--                        start after it is counted too. Pre-008 code also sends api/worker
--                        for initial launches; those stay counted in the child window.
CREATE OR REPLACE FUNCTION tester_platform.reserve_restart(p_id uuid,p_sha text,p_role text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE
    budget tester_platform.restart_budgets%ROWTYPE;
    kind text;
    digest text;
    recent timestamptz[];
BEGIN
    IF p_role IS NULL OR NOT (
        p_role ~ '^(host|host-stop):[0-9a-f]{32}$'
        OR p_role IN ('host','api','worker','api-start','worker-start')
    ) THEN
        RAISE EXCEPTION 'restart reservation denied';
    END IF;
    kind := CASE WHEN p_role='host' THEN 'host-legacy' ELSE split_part(p_role, ':', 1) END;
    SELECT * INTO budget FROM tester_platform.restart_budgets
    WHERE installation_id=p_id AND source_sha=p_sha
    FOR UPDATE;
    IF NOT FOUND OR (budget.disabled AND kind <> 'host-stop') THEN
        RAISE EXCEPTION 'restart reservation denied';
    END IF;
    IF kind IN ('host','host-stop') THEN
        digest := encode(sha256(convert_to(split_part(p_role, ':', 2), 'UTF8')), 'hex');
    END IF;
    IF kind IN ('api-start','worker-start') THEN
        RETURN;  -- Identity, row lock and kill switch only; initial launches are not crashes.
    ELSIF kind='host-stop' THEN
        IF budget.host_run_digest IS DISTINCT FROM digest THEN
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        UPDATE tester_platform.restart_budgets
        SET host_run_digest=NULL, host_run_started_at=NULL
        WHERE installation_id=p_id;
    ELSIF kind='host-legacy' THEN
        recent := tester_platform.restart_window_recent(budget.host_crashes);
        IF cardinality(recent) >= 3 THEN
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        -- sha256('legacy-host'): no 32-hex run token hashes to it, so no host-stop
        -- can end a legacy run and the next tokenized start is a counted crash.
        UPDATE tester_platform.restart_budgets
        SET host_crashes=recent || now(),
            host_run_digest=encode(sha256(convert_to('legacy-host', 'UTF8')), 'hex'),
            host_run_started_at=now()
        WHERE installation_id=p_id;
    ELSIF kind='host' THEN
        IF budget.host_run_digest = digest THEN
            RETURN;  -- Same run retrying after an ambiguous commit: never a second crash.
        END IF;
        recent := tester_platform.restart_window_recent(budget.host_crashes);
        IF budget.host_run_digest IS NOT NULL THEN
            -- Previous run ended without a clean-stop record: this start is a crash restart.
            IF cardinality(recent) >= 3 THEN
                RAISE EXCEPTION 'restart reservation denied';
            END IF;
            recent := recent || now();
        END IF;
        UPDATE tester_platform.restart_budgets
        SET host_crashes=recent, host_run_digest=digest, host_run_started_at=now()
        WHERE installation_id=p_id;
    ELSIF kind='api' THEN
        recent := tester_platform.restart_window_recent(budget.api_crashes);
        IF cardinality(recent) >= 6 THEN
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        UPDATE tester_platform.restart_budgets SET api_crashes=recent || now()
        WHERE installation_id=p_id;
    ELSE
        recent := tester_platform.restart_window_recent(budget.worker_crashes);
        IF cardinality(recent) >= 6 THEN
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        UPDATE tester_platform.restart_budgets SET worker_crashes=recent || now()
        WHERE installation_id=p_id;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION tester_platform.restart_counts(p_id uuid,p_sha text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE counts jsonb;
BEGIN
    SELECT jsonb_build_object(
        'host',cardinality(tester_platform.restart_window_recent(host_crashes)),
        'api',cardinality(tester_platform.restart_window_recent(api_crashes)),
        'worker',cardinality(tester_platform.restart_window_recent(worker_crashes)))
    INTO counts FROM tester_platform.restart_budgets
    WHERE installation_id=p_id AND source_sha=p_sha AND NOT disabled;
    IF counts IS NULL THEN
        RAISE EXCEPTION 'restart reservation denied';
    END IF;
    RETURN counts;
END;
$$;

-- Owner-only diagnosis and reviewed reset. SECURITY INVOKER: the App role has no
-- table privilege, so even an accidental grant could not read or reset anything.
-- Reset never clears the owner kill switch (disabled); that remains an explicit UPDATE.
CREATE FUNCTION tester_platform.restart_window_status(p_id uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path=pg_catalog AS $$
    SELECT jsonb_build_object(
        'installation_id',installation_id,'source_sha',source_sha,'disabled',disabled,
        'window_seconds',3600,'limits',jsonb_build_object('host',3,'api',6,'worker',6),
        'host_run_active',host_run_digest IS NOT NULL,'host_run_started_at',host_run_started_at,
        'host_crashes',to_jsonb(host_crashes),'api_crashes',to_jsonb(api_crashes),
        'worker_crashes',to_jsonb(worker_crashes),
        'legacy_lifetime_starts',jsonb_build_object(
            'host',host_starts,'api',api_starts,'worker',worker_starts))
    FROM tester_platform.restart_budgets WHERE installation_id=p_id
$$;

CREATE FUNCTION tester_platform.reset_restart_window(p_id uuid)
RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
    UPDATE tester_platform.restart_budgets
    SET host_crashes='{}', api_crashes='{}', worker_crashes='{}',
        host_run_digest=NULL, host_run_started_at=NULL
    WHERE installation_id=p_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown restart installation';
    END IF;
    RETURN tester_platform.restart_window_status(p_id);
END;
$$;

REVOKE ALL ON FUNCTION tester_platform.restart_window_recent(timestamptz[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.reserve_restart(uuid,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.restart_counts(uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.restart_window_status(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.reset_restart_window(uuid) FROM PUBLIC;
REVOKE ALL ON tester_platform.restart_budgets FROM PUBLIC;
