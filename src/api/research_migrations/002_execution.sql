-- Explicit operator migration only; no product/native startup activation.
CREATE TABLE tester_research.execution_inputs (
 input_digest char(64) PRIMARY KEY CHECK(input_digest ~ '^[a-f0-9]{64}$'),
 value jsonb NOT NULL CHECK(octet_length(value::text)<=16777216)
);
CREATE TRIGGER immutable_execution_input BEFORE UPDATE OR DELETE ON tester_research.execution_inputs
 FOR EACH ROW EXECUTE FUNCTION tester_research.reject_mutation();
CREATE TABLE tester_research.execution_grants (
 actor text REFERENCES tester_research.credentials(actor),
 input_digest char(64) REFERENCES tester_research.execution_inputs(input_digest),
 PRIMARY KEY(actor,input_digest)
);
CREATE TABLE tester_research.execution_jobs (
 job_id uuid PRIMARY KEY, actor text NOT NULL REFERENCES tester_research.credentials(actor),
 request_id uuid NOT NULL, input_digest char(64) NOT NULL REFERENCES tester_research.execution_inputs(input_digest),
 state text NOT NULL CHECK(state IN ('pending','running','complete','failed')),
 lease_token uuid, lease_until timestamptz, attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 chart_command jsonb, reason text CHECK(reason='ATTEMPT_LIMIT'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(actor,request_id),
 CHECK((state='complete')=(chart_command IS NOT NULL)),
 CHECK((state='running')=(lease_token IS NOT NULL AND lease_until IS NOT NULL)),
 CHECK((state='failed')=(reason IS NOT NULL))
);
