CREATE TABLE tester_catalog.snapshots (
    category text PRIMARY KEY CHECK (category IN ('linear','inverse')),
    schema_version text NOT NULL DEFAULT '1.0' CHECK (schema_version = '1.0'),
    snapshot jsonb CHECK (snapshot IS NULL OR (jsonb_typeof(snapshot)='object' AND octet_length(snapshot::text) <= 67108864)),
    attempt jsonb NOT NULL DEFAULT '{"completeness":"UNKNOWN","loaded":0,"last_error":null,"at":null}' CHECK (jsonb_typeof(attempt)='object' AND octet_length(attempt::text) <= 1048576),
    pending boolean NOT NULL DEFAULT false,
    next_refresh_at double precision NOT NULL DEFAULT 0,
    eligible_at double precision NOT NULL DEFAULT 0,
    failures integer NOT NULL DEFAULT 0 CHECK (failures BETWEEN 0 AND 3)
);
INSERT INTO tester_catalog.snapshots(category) VALUES ('linear'),('inverse');
