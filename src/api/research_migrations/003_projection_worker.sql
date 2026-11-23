-- Operator-migrated index for the existing single worker's bounded projection poll.
-- No producer, fixture, credential, engine or automatic recovery is activated.
CREATE INDEX research_pending_projection_worker
ON tester_research.jobs(created_at, job_id)
WHERE state IN ('pending','running') AND attempts < 3;
