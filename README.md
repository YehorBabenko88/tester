# TESTER

TESTER combines the public Bybit historical-data terminal, API, worker, dedicated PostgreSQL and MASTER with four Guardian proposal engines. Real trading and private exchange endpoints are forbidden. `GUARDIANS_ENABLED=false` remains the default; automatic Guardian actions require separate acceptance.

The canonical release entry point is `Setup-Tester.ps1` at the root of a verified release package. It provisions a private CPython 3.14.10 runtime and an isolated PostgreSQL cluster. Never point the installer at an existing PostgreSQL service or reuse system port 5432.

The release remains **NO_GO** until its exact SHA has passed the required checks, isolated Windows lifecycle acceptance, independent Guardian acceptance and production signing. An unsigned staging directory is a build result and cannot be installed as a trusted production release.

- [Installation, operation and removal](docs/release/product-guide.md)
- [Architecture, runtime and repository map](docs/release/architecture.md)
- [Reproducible release and signing handoff](docs/release/release-process.md)
- [Repository and branch consolidation](docs/release/consolidation.md)
- [Historical acceptance evidence](docs/adminit-acceptance/)

Developer validation uses Python 3.12, `python -m pip install -e ".[dev]" -c requirements-platform.lock`, then `python -m pytest`, `python -m ruff check src/api scripts tests/platform`, and `python -m ruff format --check src/api scripts tests/platform`. PostgreSQL integration tests require a separate disposable database. Native Windows SCM tests require an elevated disposable Windows runner; skipped tests do not establish acceptance. See [release CI](.github/workflows/release-candidate.yml).

Historical evidence keeps its original revision and environment. Download completion counters describe completed registered work; historical market coverage can remain UNKNOWN. These are separate claims.
