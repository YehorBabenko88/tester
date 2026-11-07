# PR9 financial error disclosure P2 correction

Tracks existing PR9 comment6710347416 on parent d28f492c; no new issue.
All diagnostic credentials/paths in regressions are synthetic, not actual secrets.

RED: 8 new HTTP tests failed on d28f492c (39 existing financial tests passed).
A synthetic private DSN/arbitrary diagnostic was reflected as error.code, caller
status200 was accepted, and mutated exception attributes crossed the boundary.
Evidence: financial-error-red.log.

Fix: exact-str public whitelist with fixed status per code and generic503
financial_provider_error for unknown codes. Provider-supplied status is ignored.
Constructor discards raw diagnostic and replaces ValueError args with safe code.
HTTP handler independently rechecks the whitelist after provider execution, so
mutated code/status cannot bypass serialization. No raw diagnostic logging added.
Nine added regressions cover HTTP disclosure/status/mutation and an unformattable,
unhashable diagnostic object; 48 financial tests now pass within full gate.

GREEN: Windows/Python3.14 `python scripts/acceptance.py --security` PASS:
152 passed,14 skipped (DB opt-in plus symlink privilege),one upstream Starlette
TestClient warning. Runtime preflight, shared/policy pins, lint/format, OpenAPI+
financial schema drift, Bandit and pip-audit PASS. financial-error-green.log.
Independent static review: no P1/P2; no defaults, source semantics or C math changes.

No SCM/RunAs/ACL/elevation/PG changes or physical installation performed. The user
cancelled UAC; it was not retried. Root physical Draft29 remains outside this fix.
Draft9 only, no main merge, no typed provider activation.
