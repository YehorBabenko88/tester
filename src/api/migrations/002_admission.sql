CREATE TABLE tester_platform.control (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    draining boolean NOT NULL DEFAULT false,
    max_pending integer NOT NULL DEFAULT 256 CHECK(max_pending > 0),
    max_running integer NOT NULL DEFAULT 2 CHECK(max_running > 0)
);
INSERT INTO tester_platform.control(singleton) VALUES(true);
