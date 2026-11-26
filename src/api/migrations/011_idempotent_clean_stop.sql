-- Restart stop idempotency correction (additive; published 008/009/010 unchanged).
--
-- A host-stop reservation may commit durably while the client loses its ACK.
-- Retrying the same stop token must succeed without reopening a host run or
-- granting any restart. Distinguish a genuine lost ACK from an arbitrary token
-- by persisting the SHA256 digest of the most recently completed stop.
-- A new host start invalidates that receipt before the next stop can be filed.
-- Authorization remains owner-protected by the existing definer function and
-- the exact bound installation UUID/source SHA. This migration grants nothing.
DO $migration$
BEGIN
 IF NOT EXISTS(
     SELECT 1 FROM pg_class
     WHERE oid = 'tester_platform.restart_budgets'::regclass
       AND relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)
       AND relkind = 'r'
 ) THEN
     RAISE EXCEPTION 'restart budget owner invalid';
 END IF;
END $migration$;

ALTER TABLE tester_platform.restart_budgets
    ADD COLUMN host_last_stop_digest text
        CHECK (host_last_stop_digest IS NULL OR host_last_stop_digest ~ '^[0-9a-f]{64}$');

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
        RETURN;
    ELSIF kind='host-stop' THEN
        IF budget.host_run_digest IS DISTINCT FROM digest THEN
            -- Only the exact latest completed token is a valid retry. Never
            -- forgive a different token, a legacy start or a future host run.
            IF budget.host_run_digest IS NULL
               AND budget.host_last_stop_digest = digest THEN
                RETURN;
            END IF;
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        UPDATE tester_platform.restart_budgets
        SET host_run_digest=NULL, host_run_started_at=NULL,
            host_last_stop_digest=digest
        WHERE installation_id=p_id;
    ELSIF kind='host-legacy' THEN
        recent := tester_platform.restart_window_recent(budget.host_crashes);
        IF cardinality(recent) >= 3 THEN
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        UPDATE tester_platform.restart_budgets
        SET host_crashes=recent || now(),
            host_run_digest=encode(sha256(convert_to('legacy-host', 'UTF8')), 'hex'),
            host_run_started_at=now(), host_last_stop_digest=NULL
        WHERE installation_id=p_id;
    ELSIF kind='host' THEN
        IF budget.host_run_digest = digest THEN
            RETURN;
        END IF;
        recent := tester_platform.restart_window_recent(budget.host_crashes);
        IF budget.host_run_digest IS NOT NULL THEN
            IF cardinality(recent) >= 3 THEN
                RAISE EXCEPTION 'restart reservation denied';
            END IF;
            recent := recent || now();
        END IF;
        UPDATE tester_platform.restart_budgets
        SET host_crashes=recent, host_run_digest=digest,
            host_run_started_at=now(), host_last_stop_digest=NULL
        WHERE installation_id=p_id;
    ELSIF kind='api' THEN
        recent := tester_platform.restart_window_recent(budget.api_crashes);
        IF cardinality(recent) >= 6 THEN
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        UPDATE tester_platform.restart_budgets
        SET api_crashes=recent || now()
        WHERE installation_id=p_id;
    ELSE
        recent := tester_platform.restart_window_recent(budget.worker_crashes);
        IF cardinality(recent) >= 6 THEN
            RAISE EXCEPTION 'restart reservation denied';
        END IF;
        UPDATE tester_platform.restart_budgets
        SET worker_crashes=recent || now()
        WHERE installation_id=p_id;
    END IF;
END;
$$;

-- Existing App role has only its specific EXECUTE grant on this signature;
-- replacing the function retains ACL/ownership; do not widen any grants.
REVOKE ALL ON FUNCTION tester_platform.reserve_restart(uuid,text,text) FROM PUBLIC;
