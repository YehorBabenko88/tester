# ADMINIT acceptance candidate — 2026-11-08

**Current continuation:** see [fresh portable acceptance and proposed UAC](CONTINUATION_2026_11_08.md)
and `continuation-status.json`. The initial snapshot below is historical.

Status: **BLOCKED_NOT_INSTALLABLE; physical acceptance NOT_RUN.**

This directory supersedes historical physical-host statements elsewhere in this
branch for this acceptance attempt. Historical evidence is preserved, not renewed.

## Source and integration decisions

Development worktree: `integration/adminit-acceptance`, based on published physical
PR29 SHA `5a61dceef920839e2fe5aa6979eb2b612e97ebf2`; this includes MASTER
`19f306cd8314cd9ce0cf488b143a3338d2393442` and main
`0a6ce227d9463562bb18889dcc0ee747f3be9130`.

Exact archived source candidate: `5ad0d684140423f7d076d54c2fcfe7fa104e76c8`.
Tree: `ebf8788251679637b4953caf6052ba2f4c4f6eee`.

Published producer changes were cherry-picked with source SHA trailers:

| Workstream | Reviewed source heads | Decision |
|---|---|---|
| A / PR10 | a62891d3f144e8d0bd87186cb937f50505c5b650 | Include opt-in bounded current metadata; not historical completeness |
| B / PR13 | 70a3ae5f82f707365c17048b2d58379a8e2456c4 | Include five missing commits in order, bounded reader, future-horizon rejection and disabled visibility scaffold |
| C / PR20 | 3cb073586ccc9213e6cb5c5f304dce43d8e46322 | Already ancestor, retain sole analytics implementation |
| D / PR12 | 6cf9a2af19e05f57f6f3d18fbda8038d9bc0f89c | Already ancestor |
| E / PR9 | bec563edde3fd2626c516ab417a6fecd999103a9 | Include two missing commits, explicit financial requests and public error redaction |
| E native / PR28 | 891ef7f21086c69b133413f8fe95a89d99fa9937 | Equivalent native JobObject repair already present in PR29 ancestry |
| F / PR19 | 998c5bc89d1b35c9ad3047b1e50dcb1295120e31 | NOT_INTEGRATED; separate simulation/API composition needed |
| G / PR24 | 5edb722fc936a56e81a174cfe654839de9a19a9d | NOT_INTEGRATED; canonical PostgreSQL integration remains outstanding |
| H / PR26 | 8bafc315e5757b3510d1b20154b8df7445f3408c | NOT_INTEGRATED / SOURCE_UNAVAILABLE; no trader identity or source permission inferred |

No original producer branch, MASTER, main, other repository or automation changed.
No new production source was authored in this attempt. No blanket merge was used.

## Fresh verification

Python3.14 isolated development environment from `requirements-platform.lock`:

- `python scripts/acceptance.py`: 338 PASS / 84 SKIP / 57 subtests PASS;
  runtime preflight, shared contract/policy pins, lint, format, OpenAPI and
  financial-query schema checks passed. No `--integration` or `--security` claim.
- `python -m unittest discover -v`: 317 total, 72 skipped, zero failures.
- `node --test frontend/tests/*.test.js`: 19 PASS, zero failures.
- Separate `bandit -r src/api scripts -ll -q`, installed dependency `pip check`,
  and Windows PowerShell parser checks passed.
- A integration: 58 PASS / 26 subtests PASS. B integration: 34 PASS / 68 SKIP /
  10 subtests PASS. These overlap final-suite coverage; do not add the counts.

Python3.12 baseline OpenAPI export failed: fourteen HTTP422 descriptions use
`Unprocessable Entity` rather than tracked `Unprocessable Content`. It is not a
schema-field change; cross-version determinism remains unqualified. Do not
regenerate/re-pin the approved contract to hide the difference.

Initial frontend attempt lacked dependencies; npm then failed certificate-chain
verification. A bounded retry used Node's system CA store (`NODE_USE_SYSTEM_CA=1`),
retained TLS verification and installed exactly the npm lock with scripts disabled.
Final frontend tests then passed. No machine trust store/security policy changed.

A fresh independent reviewer inspected the complete source delta and found no
introduced actionable P1/P2. That was static review, not CI or physical clearance.

## Current physical observation

Local machine name: ADMINIT-JQ1IQSV; Windows11 Pro build26300, medium token,
not elevated. No tester service, no API18765 listener and no test PG55469 listener.
Neither `C:\TesterLab` nor the historical
`C:\ProgramData\TesterPhysicalTest20261107` existed during preflight.
Existing `postgresql-x64-18` runs on5432 and was not changed or queried for data.

The old GitHub checkpoint describes build26200, prepared files, private venv,
cluster, backups and UAC cancellation. Those resources are **not available at the
recorded path now**. Cause, deletion, migration and host equivalence are unknown.
Do not recreate its ownership marker or claim its prior test results as current.

No install, registration, start/stop, DB migration/restore, ACL change, UAC retry,
reboot, office-network action or real trade was performed. Development dependency
installs and tests ran only in this task's work area. No installed source hotfix.

## Manifest and remaining gates

`release-manifest.json` names the archived source commit, not this later evidence
commit. This avoids self-referential commit hashes. It lists artifact SHA256,
dependency-lock hashes, migration file hashes, modules, tests and explicit gaps.
It is a release audit manifest, not the legacy installer inventory schema.
The source ZIP is retained as a local output; no hosted build-artifact claim.

Installation is blocked until exact-candidate CI succeeds, current owned lab
bootstrap is reconciled, native/keyring deployment dependencies are fully pinned
and verified, and a concrete fresh installation payload is independently checked.
The legacy installer hardcodes old ownership; it cannot safely adopt a new lab.
SCM creation additionally needs normal Windows administrator approval. Prior
cancelled UAC must not be retried automatically.

Financial provider stays unavailable; historical knowledge remains
`UNAVAILABLE/null`, visibility stays UNENROLLED/disabled, and F/G/H are excluded.
PostgreSQL, ten live instruments, financial browser/mouse/timeframe acceptance,
simulation, crash recovery, restore and resource limits remain NOT_RUN for this
candidate. There is no current terminal URL or installed SHA to display.

Next code repair: stable cross-version OpenAPI422 description with a regression.
Next host preparation: fresh ownership/paths/dependency manifest and scoped native
installer proposal, preserving every discovered existing resource. Neither the
source test PASS nor its hash authorizes those missing gates.
