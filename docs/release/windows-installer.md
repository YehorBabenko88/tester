# Canonical Windows installation candidate

`Setup-Tester.ps1` is the canonical installer. Production is restricted to a new
`C:\TesterLab`, Windows 10 1809 / Server 2019 or newer, an administrator token,
5 GiB free disk space, and independently pinned release manifest SHA-256,
Ed25519 public-key fingerprint and source SHA. Existing legacy roots are refused.
No production signing key is supplied by this repository.

The installer verifies the full artifact inventory before executing bundled
Python; the Python verifier then verifies the signed envelope with the independent
key pin. The offline package contains `payload/`, `runtimes/python/`,
`runtimes/postgres/` and `wheelhouse/`. `requirements-release-windows314.lock` pins CPython 3.14 Windows wheels. Dependency installation uses only hash-locked
binary wheels without network access. PostgreSQL is private and binds loopback on
a dynamically selected port other than 5432. Windows registers exactly two
UUID-named virtual-account services: PostgreSQL and the existing API/worker supervisor.
No additional supervisor or PostgreSQL client is introduced. Each service logs on as its unique `NT SERVICE\<UUID name>` virtual account; only its SID may write its respective mutable directory. Runtime uses a dedicated nonsuperuser database login; owner credentials stay in the administrator-only private directory.

Services use automatic startup, dependency ordering and two SCM restart attempts
(1 second and 5 seconds), followed by no action. The supervisor retains its own
bounded child restart budget. The native host confines descendants with a Windows
Job Object; service stop drains supervisor children before the deadline. Readiness
requires installed service instance identity and database, worker and storage `ok`;
provider-degraded HTTP 503 may still represent an operational read-only installation.

`Start-ReleaseTester.ps1` starts owned services idempotently, verifies readiness and
opens the terminal. COM shortcuts use exactly `TESTER — Запуск` and
`TESTER — Удаление`, with installed script targets and installation working directory.
The uninstall shortcut invokes the single security-owned uninstaller with a protected external TrustConfig. Links are finalized before the ownership ledger is sealed. Builder creation receipts and signed artifact mappings are sealed before either service first starts; later writer-boundary files require the unique virtual account owner and exact inherited ACL.

The installation journal is `state/install.jsonl`; interruption records the exact
phase and error type. An OS lifecycle lock outside the root excludes concurrent
installation/start/removal and does not prevent safe root deletion. A completed
same-release reinstall is idempotent. Incomplete installations preserve evidence
and are refused until explicitly repaired or safely removed; no blind adoption.

## Disposable native acceptance

Never execute tests against an existing `C:\TesterLab`, ADMINIT service or system
PostgreSQL. Test mode requires `TesterReleaseTest_<32 UUID hex>` and a sibling fake
`Desktop_...`. The native fixture copies a private runtime, registers only a random
TESTER service and removes it in `finally`.

On an elevated disposable Windows runner with Python 3.12, pytest and pywin32:

```powershell
$env:TESTER_REQUIRE_NATIVE_SCM='1'
python -m pytest tests/platform/test_release_scm.py::test_real_native_scm_uuid_lifecycle_recovery -q
```

The opt-in gate fails when Windows/admin prerequisites are missing; normal portable
runs skip the native test with a concrete reason. Native acceptance checks real SCM
registration, autostart/recovery configuration, idempotent start, process failure
recovery, exhausted restart budget, graceful stop, restart and deletion. The separate COM test performs real Unicode shortcut roundtrips without elevation. It does not claim reboot acceptance.
Host reboot and existing ADMINIT installation remain owner-authorized VM gates.

## Remaining release gates

A trusted production signature, fully staged offline runtime package, full installer
and uninstaller lifecycle on a disposable Windows VM, post-reboot autostart, native writer-boundary provenance and independent review are required before production GO.
A passing contract test is not production installation evidence.
