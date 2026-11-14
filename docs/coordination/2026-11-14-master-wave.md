# MASTER wave integration checkpoint — 2026-11-14

**Integration staging branch, NOT yet a combined application candidate.** Baseline source: `3b2eef8a4139195db1a775545e83f1c1f58bf403` (PC4 immutable portable). This branch initially contains only this plan.

| Producer | Exact source SHA | Isolated target CI | Remaining |
|---|---|---|---|
| DATA #72 | `f78349b1ad7bb754baee380f59bca67f2b1d2009` | Public auxiliary contracts SUCCESS; Storage integrity SUCCESS | Live Bybit 403 ACCESS_BLOCKED, historical visibility UNKNOWN |
| RESEARCH #71 | `b11b79832d1b0d848773ff077a36e7cb0595fdcb` | Research Issue59 SUCCESS | Atomic B candles + executions revision/known-at NOT_PROVEN |
| UI #74 | `fd151168473f4add97ee286b008d4540b49ac566` | Capability browser and frontend fixtures SUCCESS | Real DATA→RESEARCH→API→browser not accepted; #70 is ancestry |
| OPS #73 | `61ccacdda2d341b526ef8ea2a334099175421d32` | OPS isolated lifecycle SUCCESS | Installed native PC4 restart BLOCKED |

These are **producer CI results, not combined-release acceptance**. Full Python/static, platform acceptance, pinned research PostgreSQL and combined synthetic F-G-D jobs are still failing on producer heads.

## Composition procedure (GitHub-only; no Codex)

1. Record exact tree and SHA of every producer and compare patches against pinned baseline. DATA, RESEARCH and OPS have no direct same-file overlaps in their current patches. UI #74 includes #70 ancestor and modifies frontend/API files; preserve ancestry when composing.
2. Compose DATA+RESEARCH first in a new test-only tree; verify migration `008_public_unknown_taker_side.sql`, migration checksum and source integrity. Never rewrite stored economic fields or convert Unknown into Buy/Sell.
3. Add UI #70+#74 and test versioned capability contract. OpenAPI v1 `Kind` missing mark/index on baseline; preserve v1, review proposed 1.1 rather than silently rewriting shared schema.
4. Keep OPS code behind explicit opt-in, not installed native launcher. Its disposable Windows/PG tests do not authorize physical PC4 restart.
5. Run exact combined-head checks: Ruff, Python/unit, PG14+PG18 migration/upgrade/reconnect, original HTTP receipt→PG→DATA→RESEARCH, FastAPI+Chromium all 13 timeframes, synthetic F→G/E→D, security/owned-imports, rollback and source manifest.
6. For any failing job, capture first concrete error and compare to baseline on the same workflow; do not call it inherited without evidence. Publish reproducible failing tests and fixes to separate branches.
7. Only after exact combined SHA CI PASS and independent review consider physical PC4 acceptance, requiring separate authorization. No main merge.

## Immutable safety and data quality

No real orders; no UAC, force-kill, reboot, SCM, installed PC4 modifications, ADMINIT/office access, PG5432 changes or other repositories. Keep real market historical completeness UNKNOWN, publication/known-at unknown, and real F admission denied. Live Bybit HTTP403 is ACCESS_BLOCKED; do not bypass or mislabel recorded fixtures as live data. Report PASS/FAIL/BLOCKED/NOT_RUN separately.


## Producer refresh — 2026-11-14 07:19

The above table is the **original pinned wave**, not the latest producer set. Latest exact heads reviewed:

| Producer | Latest SHA | Target CI | Full CI |
|---|---|---|---|
| DATA #72 | `d5bfdad445a1eefb9dc12bf2725a00f171e2c321` | Public auxiliary, Storage PG14/18 and pinned research PG **SUCCESS** | Python/static, Platform, combined browser **FAIL** |
| RESEARCH #71 | `c01c32b03aaf04923b33b0513b71776b12af1e09` | Research Issue59 and pinned research PG **SUCCESS** | Python/static, Platform, combined browser **FAIL** |
| UI #74 | `a27011713c3ee8a2c5f135b8ec2e366d49ccca7c` | UI browser, frontend fixtures and combined synthetic F-G-D **SUCCESS** | Python/static, Platform and pinned research PG **FAIL** |
| OPS #73 | `be4c82e9c2a8d4d11ae0add0d1f47ba56ece9ced` | OPS isolated pure/PG/Win32/compatibility **SUCCESS** | Python/static, Platform, pinned research PG and combined browser **FAIL** |

**Composition collision:** both DATA and RESEARCH modify `tests/platform/test_research_postgres.py`. The canonical merged test must retain both sets of negative PGHOSTADDR controls, account for container-internal `inet_server_addr()` vs runner-published host, and pass real PG14/18. Do not use last-writer-wins.

**Current staging status:** no producer source imported yet; staging remains documentation-only and non-installable. After conflict resolution, build exact-source combined candidate and run all checks. Baseline Kind enum drift and physical PC4 native restart remain blocked. Real Bybit HTTP403 ACCESS_BLOCKED, historical completeness UNKNOWN and real F admission denied.


## Conflict resolution — 2026-11-14 08:27

Reviewed current immutable producer source heads: DATA `d5bfdad445a1eefb9dc12bf2725a00f171e2c321`, RESEARCH `7ec35810434e3c9159f36712a4186595ac637efa`, UI `49a884140ee7b9953b7c0eae862f9cc9a25a4b55`, OPS `0d4649f3a7cd3595ea58757f2abaf2f1b0558667`.

**DATA/RESEARCH shared file resolved**: `tests/platform/test_research_postgres.py` has identical Git blob `402c25410ba4cc6704911148b1ec0c5bba720088` on both producer HEADs. Use this exact blob once in a composed tree. It asserts libpq client hostaddr against explicit DSN, does not confuse Docker server-side `inet_server_addr()` with published host, and rejects environment/explicit hostaddr redirection. Both producer Pinned PG target workflows succeeded.

This resolves the only previously observed same-path collision for those exact versions, **not** runtime/API integration or overall CI. The staging branch still contains no imported producer application code. Next action is a reproducible combined source commit with verified tree SHA, followed by exact combined-head CI; no deployment authorized.
