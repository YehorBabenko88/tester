# Tester-owned configured factory boundary — Issue5 comment6709082262

Base:b15b1ce12633a134b19cb71a57d7e841a7120579. Adopted the exact unchanged policy
from coordinator d945b23ab940b67f569ed85aa4c8d6e9684f8149 at
src/integration/standalone_policy.py. POLICY_PIN.json records its SHA256; acceptance
checks it and the existing immutable canonical contract pin. No v1.1 adoption.

load_factory now validates its specification before any configurable import. It
preflights the whole real import-parent chain, rejects missing/ambiguous/foreign/
escaped/shadowed sources and merged namespace paths, checks cached/spec/loader origins,
then imports verified parents one-by-one and rechecks paths before descendants. The
final loaded module and callable's defining module/source are checked before invocation.
Verified owned re-exports work; foreign callable aliases are not invoked. The owned
root stays on sys.path. None retains the disabled default; empty strings are invalid.

This is trusted local configuration ownership validation, not arbitrary-Python
isolation. Owned initializers and host import finders are trusted code. An owned
initializer can execute before a post-import metadata mismatch is detected, but the
rejected factory is not invoked. No transitive business-logic or market-authenticity
certificate is inferred from ownership. No external-repository Gateway/provider is
introduced or configured, and no TI pipeline is created.

Tests first reproduced rejected specifications being imported/invoked by the old
loader, ambiguous package selection, cached foreign target invocation, path shadowing
and loaded-origin mutation. Final loader-specific gate:24PASS/1file-symlink-privilege
skip. A Windows directory junction escape was actually created in a private fixture
and rejected without symlink privilege. Positive module/package/re-export integration
factories and an actual TestClient canonical response through a configured owned
integration factory passed. This is synthetic fixture data, not live market history.

Full command python scripts/acceptance.py --integration --security:116PASS/2Windows
symlink privilege skips/1upstream httpx warning, exit0 on disposable PostgreSQL18 and
Python3.14. API/worker/DB startup and graceful stop, preflight, pins, lint/format/
OpenAPI, Bandit medium/high and pip-audit passed; PostgreSQL stopped cleanly. Existing
b15b grid/cadence, request binding, queue and log regressions are retained unchanged.

Independent static review found no P1/P2 findings in the ownership chain implementation.
Wheel built with pip wheel --no-deps; ZIP inspection verified the guard, exact policy,
policy manifest and canonical JSON schema were included. This checks artifact contents,
not an installed service or a full pipeline. No default provider activation, user DB
migration, source collection, native service install, main merge or deployment.


Additional bootstrap regression: a fresh Python process exposed a foreign regular
src.integration parent winning over the owned namespace during normal validator
import. The fix bootstraps the exact anchored policy file without parent imports,
preserves its canonical module identity, and rejects cached-policy origin mismatches.
Fresh-process foreign-parent and cached-policy tests pass; no foreign initializer or
factory is invoked by this bootstrap. The policy source itself remains unchanged.
