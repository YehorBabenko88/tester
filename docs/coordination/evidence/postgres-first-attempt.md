# Disposable PostgreSQL acceptance evidence

PostgreSQL 18 binaries: C:/Program Files/PostgreSQL/18/bin. Created exclusively new scratch cluster work/pg-master-test, owned by sandbox account LS4, superuser acceptance_admin, UTF8 locale C. No existing clusters/services/roles/databases modified. Port 55439 had no listener on non-destructive Get-NetTCPConnection check. initdb succeeded; pg_ctl start succeeded with explicit `-h 127.0.0.1 -p 55439`. Server log: work/pg-master-test/server.log.

Test suite: work/repo/test_postgres_integration.py. Discovery without opt-in: 2 tests skipped, OK. Opt-in guard requires exact BYBIT_TEST_POSTGRES=1, PGHOST=127.0.0.1, PGPORT=55439, PGDATABASE=bybit_master_acceptance, PGUSER=acceptance_admin. Suite initializes fresh database twice, tests exact numeric idempotence for ten synthetic market symbols, stages 1501 archive rows with invalid final row to test rollback, retries valid archive, verifies uniqueness/cache and executes clusters for 1/W/M with finite exact totals. Test input is synthetic, not live exchange evidence.

Attempted real execution after engine owner's final release:

    ../venv/Scripts/python.exe -m unittest test_postgres_integration -v

Result: setUpClass ERROR, 0 tests executed. Exact exception:

    psycopg.OperationalError: connection failed: connection to server at "127.0.0.1", port 55439 failed: Permission denied (0x0000271D/10013)
        Is the server running on that host and accepting TCP/IP connections?

This is a sandbox TCP permission blocker, not a PostgreSQL assertion failure. No real PostgreSQL acceptance success is claimed. No retries against different servers, firewall changes, existing credentials or network settings were attempted. Cleanup attempted `pg_ctl -D work/pg-master-test -m fast -w stop`; it reported `could not send stop signal (PID: 26792): No such process`. The background server had already exited between exec calls. Subsequent process/port inspection showed no process or listener, and pg_ctl status reported no server running. Cluster remains as evidence and can be rerun only after obtaining network access or in configured isolated CI.

