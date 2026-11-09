# Independent PR55 composition review

Exact reviewed commit `e3adfe9ea5960bbe29d5093d2e90538c821449b3`, isolated git-archive snapshot `work/nov9/combined-review`. Source read only; no source/test edits in root or snapshot. Own probe/evidence files live outside snapshot.

No blocking defect found in the bounded explicit ASGI composition. `src/integration/combined_routes.py` routes two precise research prefixes to the actual child ASGI application, preserving child Host/Origin/auth middleware and original path; these routes precede the market catch-all static mount. Route objects intentionally leave methods unrestricted at the wrapper so the child decides supported methods/auth, rather than the wrapper accidentally defaulting to GET. Unknown methods receive405. Research submit/result/ack dependencies remain in the child. Parent middleware still executes too. No automatic services, workers, migrations, server startup or credentials are added by compose_research.

Lifecycle order is market-start, research-start, research-stop, market-stop. Nested async context managers correctly unwind market startup if research startup fails; independent injected child shutdown failure also unwinds parent shutdown and propagates error. Duplicate composition is refused. The caller remains responsible for passing the reviewed persistent child and its actual lifecycle; this helper does not turn the inert persistent control factory into an automatic worker or certify its DB ownership. Current parameterized integration test uses the persistent child and explicit dispatch, not an automatic worker; comments accurately say catalog acquisition is disabled and this is not full-catalog/download/browser acceptance.

Executed in isolated snapshot:
```
../../venv/Scripts/python.exe -m pytest -q tests/platform/test_combined_routes.py --basetemp=../pytest-combined-review
```
2 passed, one dependency deprecation warning,0.40s. Covers route ordering/auth/Origin/Host/cross-site guard, both lifespans, child startup failure.

Independent `work/nov9/combined-probes.py`, run with snapshot PYTHONPATH and same venv: PASS eight in-process auth/Origin/Host/method probes (POST submit/ack unauthorized401, PUT/DELETE/OPTIONS405; foreign Origin/cross-site403; foreign Host400), no raw authenticator diagnostic disclosure; duplicate composition rejection; injected child shutdown failure parent unwind. Log `combined-probes.log`. No socket listener or external HTTP.

`tests/platform/test_research_postgres.py` combined=False/True test read statically ONLY. It composes real market routes with actual persistent child, exercises G HTTP roundtrip on its explicitly disposable fixture, but necessarily starts uvicorn/uses PostgreSQL, so it was not executed under this task's no-host-server/PG restriction. Its finally sets should_exit, joins up to5s and closes listener; it does not assert thread termination after timeout. This is an existing test-cleanup evidence limitation, not proof of a composition lifecycle leak. No PostgreSQL/real server/browser/SCM/host acceptance claim made here.
