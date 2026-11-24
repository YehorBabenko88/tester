-- Add only the two new dispatch EXECUTEs to already owner-enrolled runtimes.
-- Published migrations 004/005/006 remain immutable. No policy or job reset.
DO $migration$
DECLARE owner_id oid; function_owner oid; is_definer boolean; signature text; runtime record;
BEGIN
 SELECT oid INTO STRICT owner_id FROM pg_roles WHERE rolname=current_user;
 IF NOT EXISTS(SELECT 1 FROM pg_class WHERE oid='tester_platform.transport_policy_scopes'::regclass
               AND relowner=owner_id AND relkind='r')
 THEN RAISE EXCEPTION 'public dispatch scope owner invalid'; END IF;
 FOREACH signature IN ARRAY ARRAY[
   'tester_platform.reserve_public_dispatch(uuid)',
   'tester_platform.finish_public_dispatch(uuid,uuid,double precision)'
 ] LOOP
   SELECT proowner,prosecdef INTO function_owner,is_definer FROM pg_proc
     WHERE oid=to_regprocedure(signature);
   IF NOT FOUND OR function_owner IS DISTINCT FROM owner_id OR is_definer IS DISTINCT FROM true
   THEN RAISE EXCEPTION 'public dispatch function owner invalid'; END IF;
 END LOOP;
 -- Validate every enrollment before granting anything. A foreign or privileged
 -- role fails this complete migration transaction, rather than widening authority.
 IF EXISTS(
   SELECT 1 FROM tester_platform.transport_policy_scopes s
   LEFT JOIN pg_roles r ON r.rolname=s.runtime_role
   WHERE s.owner_role IS DISTINCT FROM current_user OR s.runtime_role=s.owner_role
      OR r.oid IS NULL OR NOT r.rolcanlogin OR r.rolsuper OR r.rolcreatedb
      OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
 ) THEN RAISE EXCEPTION 'public dispatch runtime enrollment invalid'; END IF;
 FOR runtime IN SELECT DISTINCT runtime_role FROM tester_platform.transport_policy_scopes LOOP
   EXECUTE format('GRANT EXECUTE ON FUNCTION tester_platform.reserve_public_dispatch(uuid), tester_platform.finish_public_dispatch(uuid,uuid,double precision) TO %I',runtime.runtime_role);
 END LOOP;
END $migration$;
