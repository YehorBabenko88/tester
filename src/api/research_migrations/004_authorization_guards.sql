-- Runtime may acquire authorization row locks without authority-table DML.
CREATE FUNCTION tester_research.lock_projection_credential(actor_key text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    PERFORM actor FROM tester_research.credentials
    WHERE actor=actor_key AND NOT revoked AND expires_at>pg_catalog.clock_timestamp()
    FOR SHARE;
    RETURN FOUND;
END $$;
CREATE FUNCTION tester_research.lock_projection_capability(actor_key text, digest_key text, action_key text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    PERFORM actor FROM tester_research.grants
    WHERE actor=actor_key AND artifact_digest=digest_key AND action=action_key
    FOR SHARE;
    RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION tester_research.lock_projection_credential(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_research.lock_projection_capability(text,text,text) FROM PUBLIC;
