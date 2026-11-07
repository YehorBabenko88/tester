ALTER TABLE tester_storage.download_jobs ADD COLUMN attempt_token text;
CREATE TABLE tester_storage.ingestion_runs (
  run_id text PRIMARY KEY CHECK(length(run_id) BETWEEN 1 AND 300),
  run_origin text NOT NULL CHECK(run_origin IN ('caller','legacy_job')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_storage.ingestion_run_jobs (
  run_id text NOT NULL REFERENCES tester_storage.ingestion_runs,
  job_key text NOT NULL REFERENCES tester_storage.download_jobs,
  PRIMARY KEY(run_id,job_key)
);
CREATE UNIQUE INDEX evidence_job_identity ON tester_storage.pagination_evidence(evidence_id,job_key);
CREATE TABLE tester_storage.ingestion_run_evidence (
  run_id text NOT NULL, job_key text NOT NULL, evidence_id text NOT NULL,
  PRIMARY KEY(run_id,job_key,evidence_id),
  FOREIGN KEY(run_id,job_key) REFERENCES tester_storage.ingestion_run_jobs,
  FOREIGN KEY(evidence_id,job_key) REFERENCES tester_storage.pagination_evidence(evidence_id,job_key)
);
CREATE TABLE tester_storage.source_observations (
  observation_id text PRIMARY KEY,
  source_id text NOT NULL REFERENCES tester_storage.record_sources,
  run_id text NOT NULL, job_key text NOT NULL, evidence_id text NOT NULL,
  source_kind text NOT NULL CHECK(source_kind IN ('rest','archive','import','unknown')),
  timestamp_precision text NOT NULL CHECK(timestamp_precision IN ('s','ms','us','ns')),
  recorded_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,job_key) REFERENCES tester_storage.ingestion_run_jobs,
  FOREIGN KEY(run_id,job_key,evidence_id) REFERENCES tester_storage.ingestion_run_evidence,
  FOREIGN KEY(evidence_id,job_key) REFERENCES tester_storage.pagination_evidence(evidence_id,job_key),
  UNIQUE(source_id,run_id,job_key,evidence_id)
);
CREATE INDEX observations_source ON tester_storage.source_observations(source_id);
CREATE INDEX observations_evidence ON tester_storage.source_observations(evidence_id);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_storage.ingestion_runs
 FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_storage.ingestion_run_jobs
 FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_storage.ingestion_run_evidence
 FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_storage.source_observations
 FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation();
-- Legacy observations are deliberately NOT fabricated from earlier source rows.
-- Their missing run/evidence relation remains explicitly visible to resolvers/scans.
