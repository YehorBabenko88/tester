-- Issue #114: owner-enrolled runtime roles never DELETE/TRUNCATE durable
-- platform jobs or storage checkpoints/download jobs. Additive correction for
-- databases provisioned with the former broad "SELECT,INSERT,UPDATE,DELETE ON
-- ALL TABLES" grant; fresh installs grant the same model directly
-- (src/api/release_database.py RUNTIME_UPDATE_TABLES). Published migrations stay
-- immutable; no row is read, changed or deleted here.
DO $migration$
DECLARE runtime record; target text;
BEGIN
 -- Same enrollment proof as 007: only owner-enrolled, unprivileged login roles.
 IF EXISTS(
   SELECT 1 FROM tester_platform.transport_policy_scopes s
   LEFT JOIN pg_roles r ON r.rolname=s.runtime_role
   WHERE s.owner_role IS DISTINCT FROM current_user OR s.runtime_role=s.owner_role
      OR r.oid IS NULL OR NOT r.rolcanlogin OR r.rolsuper OR r.rolcreatedb
      OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls
 ) THEN RAISE EXCEPTION 'runtime least-privilege enrollment invalid'; END IF;
 FOR runtime IN SELECT DISTINCT runtime_role FROM tester_platform.transport_policy_scopes LOOP
   EXECUTE format('REVOKE DELETE,TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA tester_platform FROM %I',
                  runtime.runtime_role);
   IF to_regnamespace('tester_storage') IS NOT NULL THEN
     EXECUTE format('REVOKE UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA tester_storage FROM %I',
                    runtime.runtime_role);
     -- Runtime writes: Store.ingest job claims (FOR UPDATE / ON CONFLICT DO
     -- UPDATE) and checkpoint upserts. Re-grant only where INSERT already exists.
     FOREACH target IN ARRAY ARRAY['download_jobs','ingestion_checkpoints'] LOOP
       IF to_regclass('tester_storage.'||target) IS NOT NULL
          AND has_table_privilege(runtime.runtime_role,'tester_storage.'||target,'INSERT') THEN
         EXECUTE format('GRANT UPDATE ON tester_storage.%I TO %I',target,runtime.runtime_role);
       END IF;
     END LOOP;
   END IF;
 END LOOP;
END
$migration$;
