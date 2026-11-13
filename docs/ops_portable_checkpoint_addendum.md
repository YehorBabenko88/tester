

## Additional guarded recovery (second commit)

`scripts/portable_ops_guard.py` provides a **separate fail-closed adapter contract**:
birth-bound externally observed clean-exit/drain witness, exact image and argv,
checkpoint owner verification, authoritative durable-state completeness, and a
transactional single-winner job-claim callback. It never launches/stops a
process itself. Do not use a non-atomic in-memory claim in production.

`ResourcePolicy.evaluate` reports RSS/CPU/thread threshold violations; it
does not enforce hard OS caps. `sustained_metrics` requires exactly ten symbol
identities and a minimum sample count; a synthetic fixture is not evidence of
real ten-symbol load or duration.

Run: `python -m unittest tests.platform.test_portable_ops_guard -v`.
Expected six additional pure tests. Windows process-handle witnesses, actual
worker integration, PostgreSQL transactional claims, time-series persistence,
hard resource enforcement and real sustained load remain **NOT_RUN/NOT_READY**.
No physical machine or production service was accessed.
