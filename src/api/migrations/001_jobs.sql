CREATE TABLE tester_platform.jobs (
    id uuid PRIMARY KEY,
    request jsonb NOT NULL,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','running','paused','completed','failed')),
    progress double precision NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    checkpoint jsonb NOT NULL DEFAULT '{}',
    attempts integer NOT NULL DEFAULT 0,
    error text,
    lease_token uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_claim ON tester_platform.jobs(state, lease_until, created_at);
CREATE TABLE tester_platform.workers (
    id uuid PRIMARY KEY,
    heartbeat_at timestamptz NOT NULL DEFAULT now()
);
