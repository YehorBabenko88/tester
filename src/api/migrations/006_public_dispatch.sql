-- Fixed, installation-wide <=2 requests/second across API, worker and restarts.
-- A durable in-flight token fences stalled/dead senders without holding a SQL
-- transaction across network I/O. No timeout can authorize an uncertain send.
ALTER TABLE tester_platform.transport_policy_scopes
 ADD COLUMN dispatch_token uuid,
 ADD COLUMN dispatch_not_before timestamptz NOT NULL DEFAULT '1970-01-01 00:00:00+00';

CREATE FUNCTION tester_platform.reserve_public_dispatch(p_scope uuid)
RETURNS TABLE(policy_state text,request_token uuid,wait_seconds double precision)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s tester_platform.transport_policy_scopes%ROWTYPE; t uuid; now_at timestamptz;
BEGIN
 PERFORM tester_platform._check_public_transport(p_scope,NULL);
 SELECT * INTO STRICT s FROM tester_platform.transport_policy_scopes WHERE installation_id=p_scope;
 now_at:=clock_timestamp();
 IF s.dispatch_not_before IS NULL OR NOT isfinite(s.dispatch_not_before)
    OR s.dispatch_not_before>now_at+interval '3600 seconds'
 THEN RAISE EXCEPTION 'public dispatch authority invalid'; END IF;
 IF s.globally_halted THEN RETURN QUERY SELECT 'HALTED'::text,NULL::uuid,0::double precision; RETURN; END IF;
 IF s.dispatch_token IS NOT NULL THEN RETURN QUERY SELECT 'PENDING'::text,NULL::uuid,0::double precision; RETURN; END IF;
 IF s.dispatch_not_before>now_at THEN
   RETURN QUERY SELECT 'WAIT'::text,NULL::uuid,extract(epoch FROM (s.dispatch_not_before-now_at))::double precision; RETURN;
 END IF;
 t:=gen_random_uuid();
 UPDATE tester_platform.transport_policy_scopes SET dispatch_token=t WHERE installation_id=p_scope;
 RETURN QUERY SELECT 'GRANTED'::text,t,0::double precision;
END $$;

CREATE FUNCTION tester_platform.finish_public_dispatch(p_scope uuid,p_token uuid,p_cooldown double precision)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s tester_platform.transport_policy_scopes%ROWTYPE; now_at timestamptz;
BEGIN
 PERFORM tester_platform._check_public_transport(p_scope,NULL);
 SELECT * INTO STRICT s FROM tester_platform.transport_policy_scopes WHERE installation_id=p_scope;
 now_at:=clock_timestamp();
 IF p_token IS NULL OR p_cooldown IS NULL OR NOT (p_cooldown>=0 AND p_cooldown<=3600)
    OR s.dispatch_not_before IS NULL OR NOT isfinite(s.dispatch_not_before)
    OR s.dispatch_not_before>now_at+interval '3600 seconds'
 THEN RAISE EXCEPTION 'public dispatch completion invalid'; END IF;
 IF s.dispatch_token IS DISTINCT FROM p_token THEN RETURN false; END IF;
 UPDATE tester_platform.transport_policy_scopes SET dispatch_token=NULL,
   dispatch_not_before=greatest(dispatch_not_before,now_at+make_interval(secs=>greatest(0.5,p_cooldown)))
 WHERE installation_id=p_scope;
 RETURN true;
END $$;

-- Only explicit whole-scope owner review may abandon a crashed dispatch and
-- clear the shared cooldown. Endpoint-only reset leaves other senders fenced.
CREATE OR REPLACE FUNCTION tester_platform.reset_public_transport(p_scope uuid,p_key text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE s tester_platform.transport_policy_scopes%ROWTYPE; n integer;
BEGIN
 PERFORM tester_platform._check_public_transport(p_scope,p_key);
 SELECT * INTO STRICT s FROM tester_platform.transport_policy_scopes WHERE installation_id=p_scope;
 IF session_user<>s.owner_role THEN RAISE EXCEPTION 'owner review required'; END IF;
 IF p_key IS NULL THEN
   DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope;
   UPDATE tester_platform.transport_policy_scopes SET globally_halted=false,dispatch_token=NULL,
     dispatch_not_before='1970-01-01 00:00:00+00' WHERE installation_id=p_scope;
   RETURN true;
 END IF;
 DELETE FROM tester_platform.transport_policy_entries WHERE installation_id=p_scope AND endpoint_key=p_key;
 GET DIAGNOSTICS n=ROW_COUNT; RETURN n=1;
END $$;
REVOKE ALL ON FUNCTION tester_platform.reserve_public_dispatch(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.finish_public_dispatch(uuid,uuid,double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION tester_platform.reset_public_transport(uuid,text) FROM PUBLIC;
