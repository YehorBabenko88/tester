-- Runtime roles may reserve launches, never edit/reset the durable authority.
CREATE TABLE tester_platform.restart_budgets (
    installation_id uuid PRIMARY KEY,
    source_sha text NOT NULL CHECK (source_sha ~ '^[0-9a-f]{40}$'),
    host_starts integer NOT NULL DEFAULT 0 CHECK (host_starts BETWEEN 0 AND 3),
    api_starts integer NOT NULL DEFAULT 0 CHECK (api_starts BETWEEN 0 AND 6),
    worker_starts integer NOT NULL DEFAULT 0 CHECK (worker_starts BETWEEN 0 AND 6),
    disabled boolean NOT NULL DEFAULT false
);

CREATE FUNCTION tester_platform.reserve_restart(p_id uuid,p_sha text,p_role text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF p_role NOT IN ('host','api','worker') OR p_role IS NULL THEN
        RAISE EXCEPTION 'restart reservation denied';
    END IF;
    UPDATE tester_platform.restart_budgets
    SET host_starts=host_starts+CASE WHEN p_role='host' THEN 1 ELSE 0 END,
        api_starts=api_starts+CASE WHEN p_role='api' THEN 1 ELSE 0 END,
        worker_starts=worker_starts+CASE WHEN p_role='worker' THEN 1 ELSE 0 END
    WHERE installation_id=p_id AND source_sha=p_sha AND NOT disabled
      AND (p_role<>'host' OR host_starts<3)
      AND (p_role<>'api' OR api_starts<6)
      AND (p_role<>'worker' OR worker_starts<6);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'restart reservation denied';
    END IF;
END;
$$;

CREATE FUNCTION tester_platform.restart_counts(p_id uuid,p_sha text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE counts jsonb;
BEGIN
    SELECT jsonb_build_object('host',host_starts,'api',api_starts,'worker',worker_starts)
    INTO counts FROM tester_platform.restart_budgets
    WHERE installation_id=p_id AND source_sha=p_sha AND NOT disabled;
    IF counts IS NULL THEN
        RAISE EXCEPTION 'restart reservation denied';
    END IF;
    RETURN counts;
END;
$$;

REVOKE ALL ON FUNCTION tester_platform.reserve_restart(uuid,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.restart_counts(uuid,text) FROM PUBLIC;
REVOKE ALL ON tester_platform.restart_budgets FROM PUBLIC;
