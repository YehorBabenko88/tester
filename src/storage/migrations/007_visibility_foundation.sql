-- Experimental foundation only. No runtime-role grants or existing writer changes.
CREATE SCHEMA tester_visibility;
CREATE TABLE tester_visibility.epochs (
  epoch_id text PRIMARY KEY CHECK(length(epoch_id) BETWEEN 1 AND 200),
  protocol_version integer NOT NULL DEFAULT 1 CHECK(protocol_version=1),
  status text NOT NULL CHECK(status='UNENROLLED'),
  success_enabled boolean NOT NULL CHECK(success_enabled=false),
  external_restore_trust text NOT NULL DEFAULT 'UNVERIFIED' CHECK(external_restore_trust='UNVERIFIED'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_visibility.scope_generations (
  epoch_id text NOT NULL REFERENCES tester_visibility.epochs,
  scope_key text NOT NULL CHECK(length(scope_key) BETWEEN 1 AND 200),
  generation bigint NOT NULL CHECK(generation>0),
  PRIMARY KEY(epoch_id,scope_key)
);
CREATE TABLE tester_visibility.mutation_events (
  operation_id text PRIMARY KEY,
  epoch_id text NOT NULL REFERENCES tester_visibility.epochs,
  scopes text[] NOT NULL CHECK(cardinality(scopes) BETWEEN 1 AND 64),
  mutator_tables text[] NOT NULL CHECK(cardinality(mutator_tables) BETWEEN 1 AND 32),
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tester_visibility.capture_drafts (
  capture_id text PRIMARY KEY,
  epoch_id text NOT NULL REFERENCES tester_visibility.epochs,
  protocol_version integer NOT NULL DEFAULT 1 CHECK(protocol_version=1),
  state text NOT NULL DEFAULT 'INCOMPLETE' CHECK(state='INCOMPLETE'),
  success_enabled boolean NOT NULL DEFAULT false CHECK(success_enabled=false),
  capture_observed_by_ms bigint CHECK(capture_observed_by_ms IS NULL),
  receipt_publication_observed_by_ms bigint CHECK(receipt_publication_observed_by_ms IS NULL),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_visibility.epochs
 FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_visibility.mutation_events
 FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON tester_visibility.capture_drafts
 FOR EACH ROW EXECUTE FUNCTION tester_storage.reject_mutation();
-- No enrollment, receipt certificate table, clock trust or physical-file assertion.
