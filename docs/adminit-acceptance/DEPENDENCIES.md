# Windows lab runtime dependencies

`requirements-windows-lab.lock` is the reproducible **CPython 3.14, Windows AMD64** runtime closure. It contains 35 pinned distributions with SHA-256 hashes of the actual downloaded wheels. It excludes pytest, HTTPX test clients, Ruff, Bandit, pip-audit and other development tools. API, ingestion HTTP, JSON Schema validation, PostgreSQL driver, Windows SCM/JobObject bindings and the Windows Credential Manager backend are included.

The API/storage/requests/jsonschema versions and their shared dependencies match `requirements-platform.lock`. Added Windows dependencies are pywin32 312, keyring 25.7.0, pywin32-ctypes 0.2.3, jaraco.classes 3.4.0, jaraco.context 6.1.2, jaraco.functools 4.6.0 and more-itertools 11.2.1. Hashes cover these platform wheels; this lock is intentionally not a Linux or Python 3.12 lock.

## Build and install

Use a CPython 3.14 AMD64 interpreter. The wheelhouse and virtual environments belong outside the source repository. From the repository root:

```powershell
python -m pip download --only-binary=:all: --require-hashes -r requirements-windows-lab.lock --dest ..\lab-wheelhouse
python -m venv ..\lab-cleanenv
..\lab-cleanenv\Scripts\python.exe -m pip install --no-index --find-links ..\lab-wheelhouse --only-binary=:all: --require-hashes -r requirements-windows-lab.lock
..\lab-cleanenv\Scripts\python.exe -m pip check
```

Download uses pip's configured package index; no alternate index or unverified direct archive URL is embedded. The lock hashes authenticate the selected artifacts. Keep the source GitHub-first: publish/review the source change before any physical lab runtime deployment. Installing this development verification environment does not authorize deploying a service or changing host/database configuration.

## Verification evidence (2026-11-08)

Clean environment: sibling `work/lab-cleanenv`; wheelhouse: sibling `work/lab-wheelhouse`. Interpreter: CPython 3.14.0, MSC v.1944, 64 bit AMD64. All 35 distributions installed offline using `--no-index --require-hashes --only-binary=:all:`; `pip check` reported no broken requirements. Actual `win32service`, `win32job` and `servicemanager` imports succeeded. `keyring.backends.Windows.WinVaultKeyring` imported and was the selected backend. API application import succeeded. No fake Windows modules or fallback credential backend were used.

This is dependency/import evidence only. No existing credentials were read, no credentials were written, and no SCM service, JobObject, database or machine configuration was created or changed. A scoped synthetic Credential Manager roundtrip and service-account access remain part of the later physical published-runtime acceptance, not this build check.

The OpenAPI regression was observed RED on Python 3.12 (2 failed/1 passed with legacy, approved and simulated future HTTP 422 phrases). Explicit `Unprocessable Content` in the application response metadata made all three cases pass on Python 3.12 and 3.14. `scripts/export_contract.py --check` passed on both interpreters without rewriting the approved contract.
