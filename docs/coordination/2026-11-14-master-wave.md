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
