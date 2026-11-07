# UTC grid and native observation cadence — PR9 comment6708786552

Base:0a17033f6c88b336e988e4007231a2ee9cefe761. Reproduced failures before fixes:
fixed minute/day/week windows accepted off-grid starts based solely on duration;
float/bool boundaries passed; funding/OI received chart viewport as native source
interval. Existing successful tests included an incorrect Thursday-based weekly
fixture, now corrected to Monday. No shared v1.0 file was changed.

Bar validation now requires exact Python integer source timestamps and bounds,
minute-grid modulo, UTC-midnight days, Monday00:00 weeks and successive real calendar
months. Month conversion uses epoch+integer timedelta; no floating timestamps or C
market calculations. Tests include leap/common February and December→January rollover,
off-grid minute/D/W failures and successful source-bar HTTP roundtrips.

New source_interval is separate from display interval. Funding resolves event only.
OI source_interval must be5min/15min/30min/1h/4h/1d; absent means5min, regardless of
viewport text (including1h). Reader receives interval=source_interval=native cadence,
viewport_interval=original display label. Scope authority is a separate normalized
query; provider mutation cannot rewrite it. Cursor binding uses normalized native
source cadence and excludes viewport. Changing60→D can continue the same event/OI
stream; changing OI cadence fails422 before reader invocation. No resampling occurs.
For bar resources, mismatched explicit source/display interval is rejected.

D was notified of the exact mapping and explicit source_interval requirement for
nondefault OI cadence. No code in D's branch was modified. Source provenance,
identity/range/version/quality binding and old P1 regressions remain intact.

Final command: python scripts/acceptance.py --integration --security, Python3.14,
disposable PostgreSQL18 tester_acceptance database. Exit0:92PASS/1Windows-symlink
privilege skip/1upstream TestClient httpx warning. Includes24 new grid/source tests,
bounded preflight, unchanged shared-pin verification, lint/format/OpenAPI, real
HTTP/API/worker/DB graceful lifecycle, Bandit and pip-audit. PostgreSQL stopped.

TI Issue25 runtime pipeline was not created; no external-repository Gateway/provider,
native service installation, main merge or physical deployment occurred. Coordinator
ownership guard remains a separate dependency; optional price-only1.1 was not adopted.
