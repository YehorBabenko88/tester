ALTER TABLE tester_storage.download_jobs ADD COLUMN attempt_count bigint NOT NULL DEFAULT 0 CHECK(attempt_count>=0);
ALTER TABLE tester_storage.download_jobs ADD COLUMN next_attempt_ms bigint NOT NULL DEFAULT 0 CHECK(next_attempt_ms>=0);
ALTER TABLE tester_storage.download_jobs ADD COLUMN last_error_code text;
