-- Additive P0 installation principal isolation (published 004..011 immutable).
-- Every enrolled installation must own its own PostgreSQL runtime login.
-- Ambiguous upgrades FAIL atomically without changing existing policy state.
--
-- SECURITY DEFINER current_user is the SQL function owner, NOT the App login.
-- session_user is the authenticated DB login and must resolve to one scope.
-- A mere installation UUID or a copied signed file is not authorization.
DO $migration$
DECLARE
    owner_oid oid;
    guard_owner oid;
    guard_definer boolean;
BEGIN
    SELECT oid INTO STRICT owner_oid FROM pg_catalog.pg_roles
    WHERE rolname = current_user;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_class
        WHERE oid = 'tester_platform.transport_policy_scopes'::pg_catalog.regclass
          AND relkind = 'r' AND relowner = owner_oid
    ) THEN
        RAISE EXCEPTION 'transport principal scope owner invalid';
    END IF;
    SELECT proowner, prosecdef INTO guard_owner, guard_definer
    FROM pg_catalog.pg_proc
    WHERE oid = 'tester_platform._check_public_transport(uuid,text)'::pg_catalog.regprocedure;
    IF guard_owner IS DISTINCT FROM owner_oid OR guard_definer IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'transport principal guard owner/definer invalid';
    END IF;
    -- Blocks concurrent scope insertion/enrollment changes during the validation
    -- and index creation. Existing grants and policy entries are untouched.
    LOCK TABLE tester_platform.transport_policy_scopes IN SHARE ROW EXCLUSIVE MODE;
    IF EXISTS (
        SELECT 1 FROM tester_platform.transport_policy_scopes AS s
        LEFT JOIN pg_catalog.pg_roles AS r ON r.rolname = s.runtime_role
        WHERE s.owner_role IS DISTINCT FROM current_user
           OR s.runtime_role IS NULL OR s.runtime_role = s.owner_role
           OR r.oid IS NULL OR NOT r.rolcanlogin OR r.rolsuper
           OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
           OR r.rolbypassrls
    ) THEN
        RAISE EXCEPTION 'transport runtime principal enrollment invalid';
    END IF;
    IF EXISTS (
        SELECT 1 FROM tester_platform.transport_policy_scopes
        GROUP BY runtime_role HAVING count(*) <> 1
    ) THEN
        RAISE EXCEPTION 'transport runtime principal enrolled by multiple installations';
    END IF;
    -- A runtime may not be a member of another role: direct or indirect
    -- impersonation undermines the uniqueness of session principal identity.
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_auth_members AS m
        JOIN pg_catalog.pg_roles AS runtime ON runtime.oid = m.member
        JOIN tester_platform.transport_policy_scopes AS s ON s.runtime_role = runtime.rolname
    ) THEN
        RAISE EXCEPTION 'transport runtime principal has another role membership';
    END IF;

END $migration$;

-- Even under competing owner transactions only one installation can enroll
-- each SQL LOGIN. No UPDATE/DELETE/row re-enrollment, and no auto-repair.
CREATE UNIQUE INDEX transport_policy_one_installation_per_runtime_role
    ON tester_platform.transport_policy_scopes (runtime_role);

-- Validate the unique role binding on every policy call. SQL GRANT of the
-- policy functions remains unchanged and a caller UUID is never a capability.
-- Preserve all the original 004 validation: endpoint identity, advisory
-- transaction lock, row lock, corruption checks, capacity, halt policy.
-- Narrow ONLY the SQL authenticated-session principal decision.
CREATE OR REPLACE FUNCTION tester_platform._check_public_transport(p_scope uuid,p_key text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s tester_platform.transport_policy_scopes%ROWTYPE; n integer;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('tester:public-transport:'||p_scope::text,0));
 SELECT * INTO STRICT s FROM tester_platform.transport_policy_scopes WHERE installation_id=p_scope FOR UPDATE;
 IF s.runtime_role IS NULL OR s.owner_role IS DISTINCT FROM current_user
    OR session_user NOT IN (s.runtime_role,s.owner_role) OR s.schema_version IS DISTINCT FROM 1
    OR s.globally_halted IS NULL THEN RAISE EXCEPTION 'public transport scope unavailable'; END IF;
 -- Owner is restricted to owner-enrolled scopes. App sessions must be the
 -- scope's unique enrolled principal, never simply another tester_app login.
 IF session_user <> s.owner_role AND session_user <> s.runtime_role THEN
    RAISE EXCEPTION 'transport session principal not enrolled for installation';
 END IF;
 IF session_user <> s.owner_role AND (
     SELECT count(*) FROM tester_platform.transport_policy_scopes
     WHERE runtime_role=session_user
 ) <> 1 THEN
    RAISE EXCEPTION 'transport session principal ambiguous';
 END IF;
 IF p_key IS NOT NULL AND (length(p_key) NOT BETWEEN 1 AND 2048 OR
    p_key !~ '^(api[.]bybit[.]com/v5/market/|public[.]bybit[.]com/)[A-Za-z0-9_./-]*$' OR
    p_key ~ '(^|/)[.][.]?(/|$)') THEN RAISE EXCEPTION 'public transport identity invalid'; END IF;
 SELECT count(*) INTO n FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope;
 IF n>128 OR EXISTS(SELECT 1 FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope AND
   (state IS NULL OR state NOT IN ('PENDING','HALTED') OR request_token IS NULL OR
    endpoint_key IS NULL OR length(endpoint_key) NOT BETWEEN 1 AND 2048 OR
    endpoint_key !~ '^(api[.]bybit[.]com/v5/market/|public[.]bybit[.]com/)[A-Za-z0-9_./-]*$' OR
    endpoint_key ~ '(^|/)[.][.]?(/|$)' OR updated_at IS NULL OR NOT isfinite(updated_at) OR
    (state='PENDING' AND reason IS NOT NULL) OR
    (state='HALTED' AND (reason IS NULL OR reason NOT IN ('http_403','tls_failure')))))
 THEN RAISE EXCEPTION 'public transport state invalid'; END IF;
END $$;

-- Strictly preserve owner and privileges; no new EXECUTE grant.
REVOKE ALL ON FUNCTION tester_platform._check_public_transport(uuid,text) FROM PUBLIC;
