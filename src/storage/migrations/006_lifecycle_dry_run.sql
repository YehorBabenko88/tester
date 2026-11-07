CREATE TABLE tester_storage.lifecycle_objects (
  object_id text PRIMARY KEY, retention_class text NOT NULL CHECK(retention_class IN
    ('RAW_PROTECTED','VERIFIED_ARCHIVE','DERIVED_REBUILDABLE','CACHE','TEMP')),
  root_uri text NOT NULL, storage_uri text NOT NULL, canonical_uri text NOT NULL UNIQUE,
  sha256 text NOT NULL CHECK(length(sha256)=64),
  bytes bigint NOT NULL CHECK(bytes>0), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.lifecycle_object_evidence (
  object_id text NOT NULL REFERENCES tester_storage.lifecycle_objects,
  evidence_id text NOT NULL REFERENCES tester_storage.pagination_evidence,
  PRIMARY KEY(object_id,evidence_id)
);
CREATE TABLE tester_storage.lifecycle_references (
  parent_id text NOT NULL REFERENCES tester_storage.lifecycle_objects,
  child_id text NOT NULL REFERENCES tester_storage.lifecycle_objects,
  evidence_id text NOT NULL REFERENCES tester_storage.pagination_evidence,
  PRIMARY KEY(parent_id,child_id,evidence_id), CHECK(parent_id<>child_id)
);
CREATE TABLE tester_storage.backup_verifications (
  backup_id text PRIMARY KEY, manifest jsonb NOT NULL, restored boolean NOT NULL,
  data_cutoff_ms bigint NOT NULL, backup_start_ms bigint NOT NULL, backup_end_ms bigint NOT NULL,
  restore_start_ms bigint NOT NULL, restore_end_ms bigint NOT NULL,
  rpo_ms bigint NOT NULL CHECK(rpo_ms>=0), rto_ms bigint NOT NULL CHECK(rto_ms>=0),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK(0<=data_cutoff_ms AND data_cutoff_ms<=backup_start_ms AND backup_start_ms<=backup_end_ms
    AND backup_end_ms<=restore_start_ms AND restore_start_ms<=restore_end_ms)
);
CREATE TABLE tester_storage.backup_evidence (
  backup_id text NOT NULL REFERENCES tester_storage.backup_verifications,
  evidence_id text NOT NULL REFERENCES tester_storage.pagination_evidence, PRIMARY KEY(backup_id,evidence_id)
);
CREATE TABLE tester_storage.lifecycle_scans (
  scan_id text PRIMARY KEY, report jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.lifecycle_quarantine (
  object_id text NOT NULL, scan_id text NOT NULL REFERENCES tester_storage.lifecycle_scans,
  reason text NOT NULL, PRIMARY KEY(object_id,scan_id)
);
CREATE TABLE tester_storage.lifecycle_plans (
  plan_id text PRIMARY KEY, mode text NOT NULL CHECK(mode='DRY_RUN'), plan jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['lifecycle_objects','lifecycle_object_evidence','lifecycle_references',
   'backup_verifications','backup_evidence','lifecycle_scans','lifecycle_quarantine','lifecycle_plans'] LOOP
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_storage.%I FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation()',t);
 END LOOP;
END $$;
