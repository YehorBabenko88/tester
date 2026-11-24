-- Owner-seeded hard public-network policy. No Guardian effectors or expiry.
CREATE TABLE tester_platform.transport_policy_scopes (
 installation_id uuid PRIMARY KEY,
 runtime_role name NOT NULL,
 owner_role name NOT NULL,
 schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 globally_halted boolean NOT NULL DEFAULT false
);
CREATE TABLE tester_platform.transport_policy_entries (
 installation_id uuid NOT NULL REFERENCES tester_platform.transport_policy_scopes,
 endpoint_key text NOT NULL,
 request_token uuid NOT NULL,
 state text NOT NULL CHECK(state IN ('PENDING','HALTED')),
 reason text,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(installation_id,endpoint_key),
 CHECK(length(endpoint_key) BETWEEN 1 AND 2048),
 CHECK((state='PENDING' AND reason IS NULL) OR
       (state='HALTED' AND reason IN ('http_403','tls_failure')))
);

CREATE FUNCTION tester_platform._check_public_transport(p_scope uuid,p_key text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s tester_platform.transport_policy_scopes%ROWTYPE; n integer;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('tester:public-transport:'||p_scope::text,0));
 SELECT * INTO STRICT s FROM tester_platform.transport_policy_scopes WHERE installation_id=p_scope FOR UPDATE;
 IF s.runtime_role IS NULL OR s.owner_role IS DISTINCT FROM current_user
    OR session_user NOT IN (s.runtime_role,s.owner_role) OR s.schema_version IS DISTINCT FROM 1
    OR s.globally_halted IS NULL THEN RAISE EXCEPTION 'public transport scope unavailable'; END IF;
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

CREATE FUNCTION tester_platform.read_public_transport(p_scope uuid,p_key text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v text;
BEGIN
 PERFORM tester_platform._check_public_transport(p_scope,p_key);
 IF p_key IS NULL THEN RAISE EXCEPTION 'public transport identity required'; END IF;
 IF (SELECT globally_halted FROM tester_platform.transport_policy_scopes WHERE installation_id=p_scope)
 THEN RETURN 'HALTED'; END IF;
 SELECT state INTO v FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope AND endpoint_key=p_key;
 IF FOUND THEN RETURN v; END IF;
 IF (SELECT count(*) FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope)>=128
 THEN RETURN 'CAPACITY'; END IF;
 RETURN 'CLEAR';
END $$;

CREATE FUNCTION tester_platform.reserve_public_transport(p_scope uuid,p_key text)
RETURNS TABLE(policy_state text,request_token uuid) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v text; t uuid;
BEGIN
 v:=tester_platform.read_public_transport(p_scope,p_key);
 IF v<>'CLEAR' THEN RETURN QUERY SELECT v,NULL::uuid; RETURN; END IF;
 t:=gen_random_uuid();
 INSERT INTO tester_platform.transport_policy_entries(installation_id,endpoint_key,request_token,state)
 VALUES(p_scope,p_key,t,'PENDING');
 RETURN QUERY SELECT 'GRANTED'::text,t;
END $$;

CREATE FUNCTION tester_platform.finish_public_transport(p_scope uuid,p_key text,p_token uuid,p_outcome text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE n integer;
BEGIN
 PERFORM tester_platform._check_public_transport(p_scope,p_key);
 IF p_token IS NULL OR p_key IS NULL OR p_outcome IS NULL OR p_outcome NOT IN ('allow','http_403','tls_failure')
 THEN RAISE EXCEPTION 'public transport outcome invalid'; END IF;
 IF p_outcome='allow' THEN
   DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope AND endpoint_key=p_key
     AND request_token=p_token AND state='PENDING';
   GET DIAGNOSTICS n=ROW_COUNT; RETURN n=1;
 END IF;
 UPDATE tester_platform.transport_policy_entries SET state='HALTED',reason=p_outcome,updated_at=clock_timestamp()
 WHERE installation_id=p_scope AND endpoint_key=p_key AND request_token=p_token AND state IN ('PENDING','HALTED');
 GET DIAGNOSTICS n=ROW_COUNT;
 -- A late security fault after a reservation was revoked cannot be forgotten.
 IF n=0 THEN UPDATE tester_platform.transport_policy_scopes SET globally_halted=true WHERE installation_id=p_scope; END IF;
 RETURN true;
END $$;

CREATE FUNCTION tester_platform.reset_public_transport(p_scope uuid,p_key text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s tester_platform.transport_policy_scopes%ROWTYPE; n integer;
BEGIN
 PERFORM tester_platform._check_public_transport(p_scope,p_key);
 SELECT * INTO STRICT s FROM tester_platform.transport_policy_scopes WHERE installation_id=p_scope;
 IF session_user<>s.owner_role THEN RAISE EXCEPTION 'owner review required'; END IF;
 IF p_key IS NULL THEN
   DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope;
   UPDATE tester_platform.transport_policy_scopes SET globally_halted=false WHERE installation_id=p_scope;
   RETURN true;
 END IF;
 DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope AND endpoint_key=p_key;
 GET DIAGNOSTICS n=ROW_COUNT; RETURN n=1;
END $$;

REVOKE ALL ON FUNCTION tester_platform._check_public_transport(uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.read_public_transport(uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.reserve_public_transport(uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.finish_public_transport(uuid,text,uuid,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.reset_public_transport(uuid,text) FROM PUBLIC;
