-- Explicit opt-in B/E research schema. Never applied by native platform migrate().
CREATE SCHEMA IF NOT EXISTS tester_research;
CREATE TABLE tester_research.credentials (
 actor text PRIMARY KEY, token_hash char(64) NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE tester_research.artifacts (
 artifact_digest char(64) PRIMARY KEY, chart_text text NOT NULL,
 bundle_digest char(64) NOT NULL, bundle_text text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(octet_length(chart_text)<=1048576), CHECK(octet_length(bundle_text)<=8388608)
);
CREATE TABLE tester_research.grants (
 actor text REFERENCES tester_research.credentials(actor),
 artifact_digest char(64) REFERENCES tester_research.artifacts(artifact_digest),
 action text NOT NULL CHECK(action='chart.overlay'),
 PRIMARY KEY(actor,artifact_digest,action)
);
CREATE TABLE tester_research.jobs (
 job_id uuid PRIMARY KEY, actor text NOT NULL REFERENCES tester_research.credentials(actor),
 request_id uuid NOT NULL, command jsonb NOT NULL, digest char(64) NOT NULL,
 state text NOT NULL CHECK(state IN ('pending','running','complete')),
 artifact_digest char(64) REFERENCES tester_research.artifacts(artifact_digest),
 lease_token uuid, lease_until timestamptz, attempts integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(actor,request_id)
);
CREATE TABLE tester_research.acknowledgments (
 job_id uuid PRIMARY KEY REFERENCES tester_research.jobs(job_id),
 actor text NOT NULL REFERENCES tester_research.credentials(actor),
 artifact_digest char(64) NOT NULL REFERENCES tester_research.artifacts(artifact_digest),
 rendered_fill_ids jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE tester_research.audit (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor text NOT NULL, job_id uuid, event text NOT NULL,
 artifact_digest char(64), at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION tester_research.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'immutable research evidence'; END $$;
CREATE TRIGGER immutable_artifact BEFORE UPDATE OR DELETE ON tester_research.artifacts
 FOR EACH ROW EXECUTE FUNCTION tester_research.reject_mutation();
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON tester_research.audit
 FOR EACH ROW EXECUTE FUNCTION tester_research.reject_mutation();
CREATE TRIGGER immutable_ack BEFORE UPDATE OR DELETE ON tester_research.acknowledgments
 FOR EACH ROW EXECUTE FUNCTION tester_research.reject_mutation();
