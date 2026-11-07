# Bybit Historical Market Laboratory

Independent, read-only public market-data downloader and visualization tool. See the upcoming source files for operational details. No trading API keys, no order placement.

Platform workstream: [Windows setup and operations](docs/operations.md),
[API v1 contracts and adapter ports](docs/api-contract.md).

Local architecture: FastAPI on loopback, PostgreSQL-backed durable jobs, a separate
worker and supervisor, and a browser frontend supplied by the UI workstream.
Run `python scripts/acceptance.py --integration --security` against a disposable
`TEST_DATABASE_URL` after installing `.[dev]` with `requirements-platform.lock`.
Full installer acceptance runs with `scripts/windows/Test-Isolated.ps1` in Windows CI.
Market-data providers are separate A/B/C integrations; unavailable providers return
explicit errors. A platform health pass alone does not establish market-data readiness.
