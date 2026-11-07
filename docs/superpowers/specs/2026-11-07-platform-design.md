# Platform design

Implement the user's Agent E mission in workstream/e-platform. Python FastAPI runs on loopback; PostgreSQL owns a durable queue in tester_platform; separate supervised worker uses leases and fencing tokens. No Redis, trading keys or physical-machine deployment. User selected PostgreSQL worker and delegated remaining implementation decisions.

The API uses schema_version 1.0, UTC epoch milliseconds, decimal strings and explicit provenance. Provider ports isolate A ingestion, B storage and C analytics; unavailable providers return 503 instead of fabricated data. Download transitions are persisted and validated; expiration recovers crashed workers, pause revokes leases. Local browser is served only when frontend exists.

Windows setup validates prerequisites, free disk and configuration, creates only a new dedicated role/database, applies migrations, installs into a venv and starts a supervisor only on explicit invocation. Secrets remain outside git, protected with Windows ACL. Acceptance uses a disposable PostgreSQL cluster/database, never an existing user's databases. CI covers Linux/PostgreSQL integration and Windows runtime/installer execution. Document limitations and require acceptance before deployment. Alternatives considered: Redis/Celery adds unnecessary local services; in-process queue loses durability. PostgreSQL was selected by user.
