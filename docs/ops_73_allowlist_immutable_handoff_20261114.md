# OPS #73 immutable allowlist handoff to MASTER #77 — reviewed source only

Reviewed MASTER **298d610771d962ee7b796eac1834d74138768fac** and `docs/coordination/2026-11-14-producer-allowlist-review.md`. This is a recommendation, **not MASTER approval**. Frozen OPS source `36774155e1b624143cba848110bf83a5f9a6437b` (PC4 base `3b2eef8a4139195db1a775545e83f1c1f58bf403`).

## Immutable files and roles

`docs/ops_producer_allowlist_delta_36774155.json` pins the **14 source-only extra paths** by their original Git blob SHA1 and Git mode from the exact frozen source commit, not from a moving branch. There were 65 files on the old MASTER reviewed OPS allowlist and exactly 79 files changed by OPS at the frozen commit; **79 - 65 = 14**, no missing approved paths. All 14 are regular `100644` blobs.

- **Production runtime newly admitted: ZERO.** The 65 previously reviewed OPS paths remain subject to MASTER's existing review; this ledger does not reevaluate or approve them.
- **CI-only prototype (1):** `scripts/ops_private_generation_prototype.py`. It is an independent experiment, never import in native Windows/PC4 launch.
- **CI-only workflows (2):** `.github/workflows/ops-master-v3-security-audit.yml`, `.github/workflows/ops-private-generation-ci.yml`. Do not ship these into a runtime generation.
- **CI-only test modules (6):** MASTER v3/v4/v5 security negatives plus prototype/crash/recovery modules. Do not include in runtime deployment.
- **Documentation (5):** old MASTER security reports, private-generation protocol, and MASTER opt-in runbook. No executable authority.

The delta file is an **immutable source ledger** but is **not an externally signed/attested manifest**. Review its own Git blob hash separately when MASTER proposes to consume it. Additional ledger/test/report changes in the OPS branch after the frozen source commit are not automatically part of the 14-file frozen set. New `tests/platform/test_ops_producer_allowlist_delta.py` verifies original Git object identity, classification and exact delta; it is not included in the already-frozen producer set.

## Actual source and isolated CI evidence

At exact original OPS source `36774155e1b624143cba848110bf83a5f9a6437b`:
- [Private generation #43594285722](https://github.com/YehorBabenko88/tester/actions/runs/43594285722): **SUCCESS**, 17 prototype + 13 crash + 15 recovery = 45/45 PASS, no-native-activation check PASS (actual job logs inspected).
- [Independent legacy MASTER negative #43594285768](https://github.com/YehorBabenko88/tester/actions/runs/43594285768): **SUCCESS**, v2 8 + v3 20 + v4 21 + v5 24 isolated tests. Names such as `STILL_ESCAPE`, `STILL_RACE`, `STILL_PARTIAL` are **successfully reproduced vulnerabilities**, NOT security fixes.
- [OPS isolated portable #43594285721](https://github.com/YehorBabenko88/tester/actions/runs/43594285721): **SUCCESS**.
- Independent global Python #43594285802, Platform #43594285803, pinned research PostgreSQL #43594285758 and combined browser #43594285762 were **FAIL** at this source SHA. Full release **NO_GO**.
- New OPS commit with this ledger/test is a distinct head; **do not reuse the 45/45 as an exact-new-head CI claim**. Track new CI separately. New ledger verifier remains NOT_RUN unless actual CI executes it.

## Prototype protocol and security split

1. `private_root`: only absolute, non-symlink, current-UID owner-only `0700` root. `publish_generation`: exclusive fresh staged paths with `O_EXCL|O_NOFOLLOW`, `fchmod`, per-file `fsync`; exact manifest/blob hashes, forbidden path/unknown node and regular inode/hardlink checks; full-tree verified digest.
2. Seal `generation.json` with `VERIFIED`, source pins, manifest SHA256 and tree digest. Sync directories, reverify complete staging, publish a **unique directory** via rename, sync generation parent, verify published bytes again, then write and atomically replace `CURRENT` and sync root. A pre-swap fault cannot point CURRENT to partial stage. Reader `read_current` and `read_pinned` revalidate complete receipts and bytes.
3. Post-CURRENT exception and root fsync failure: a complete new generation may **already be visible**. Do not infer rollback; power-loss persistence is **UNKNOWN**. Tests are injected exceptions, not power interruption.
4. `inspect_recovery` and `recovery_candidate`: read-only, distinguish CURRENT_VALID/MISSING/INVALID/BLOCKED_UNSAFE_WORKSPACE, retain staging/orphans/pointer temp, do **not** rewire CURRENT. `retire_generation` always denies GC, even for old and orphan generations.
5. **BLOCKED** independent runner/Git trust: reviewed SHA hashes supplied by caller and forged `GITHUB_*` environment are not external attestation. **BLOCKED** malicious same-UID writer, parent directory relocation, malicious mutation after last verify and cross-process reader snapshot/lease fencing. `read_pinned` is name-based validation, not lease. **BLOCKED** real power-loss fsync persistence and safe generation GC.
6. Full BASE + DATA/RESEARCH/UI/OPS generation has **NOT_RUN** with this prototype. Current `valid_path()` rejects root files such as `pyproject.toml`; an exact reviewed root-file allowlist and whole-BASE manifest are mandatory first.

## Safe MASTER integration proposal (not executed)

1. MASTER owner explicitly reviews immutable `36774155...`, existing 65 OPS paths and this 14-path source-only delta. Keep CI-only scripts/tests/workflows/docs separate from the **production** whole-tree manifest. No blind pin to current branch head, no auto-broadening the old `master-reviewed-producer-files.json`.
2. Independently attest Git source and runner, pin all four producer commit SHAs, reviewed Base SHA, **whole-tree** manifest Git object and externally reviewed SHA256. Resolve identical overlaps only via an explicit verified blob policy. The prototype must be extended to exact allowlisted root files before whole-tree use.
3. Add **separate opt-in disposable Linux GitHub Actions workflow** (not legacy composer replacement) with no checkout writes, no credential persistence/secrets, no production host mounts or database access, separate trusted same-filesystem owner-only workspace. No source fallback, no automatic native launcher.
4. Run publish/read-current/read-pinned/full-receipt and negative fault tests on exact whole-tree source. Deny incomplete, unknown, post-fault ambiguous or corrupt generation. Recovery only diagnostic, no pointer auto-selection/GC.
5. On **one verified generation/tree SHA** execute isolated PG14 and PG18 original-HTTP/Decimal/UNKNOWN/migration acceptance, DATA→RESEARCH, Platform/Python, FastAPI/Chromium 13TF and security/combined browser. Require fresh success across all gates; no inherited source-only tests substituted for whole-tree acceptance.
6. Keep **NO_GO** until same-UID/ancestor isolation, external attestation, crash durability policy, multi-reader leases/GC and full acceptance receive independent sign-off. Native PC4 launcher, SCM, trading and installed PostgreSQL5432 are forbidden.

No MASTER #77, main, PC4, installed PostgreSQL5432 changes. No Codex, force-kill, UAC, reboot or native activation.
