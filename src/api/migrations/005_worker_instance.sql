ALTER TABLE tester_platform.workers ADD COLUMN service_instance text;
CREATE INDEX workers_service_instance ON tester_platform.workers(service_instance, heartbeat_at);
