# MASTER #77 — opt-in private-generation integration runbook (proposal only)

**Immutable review points (2026-11-14):**
- MASTER Draft #77: `5ee448fa6e0c356034e33b6f54f2ddbf6bdbc1cd`
- OPS prototype parent: `113d7f3b97f47c86a19dc8590bdbb9809138c4d8`
- PC4 source BASE: `3b2eef8a4139195db1a775545e83f1c1f58bf403`
- Frozen MASTER producer pins: DATA `d5bfdad445a1eefb9dc12bf2725a00f171e2c321`,
  RESEARCH `7ec35810434e3c9159f36712a4186595ac637efa`,
  UI `a61a2c3bceec8747d7b2424d91c9a4a6c3ec9d96`,
  OPS `0d4649f3a7cd3595ea58757f2abaf2f1b0558667`.
  These are NOT automatically replaced by later producer HEADs.
- Reviewed source-diff allowlist: MASTER
  `docs/coordination/master-reviewed-producer-files.json` at the
  immutable MASTER commit, NOT a mutable checkout file.

**Ownership:** MASTER maintainer integrates only after independent code
review. OPS makes no commit or workflow change in #77. Existing MASTER
`scripts/master_disposable_compose.py` is known race-prone and must not be
used as production installer or modified in place without separate review.

## Opt-in separate CI workflow, NOT native installation

1. Create a new, manually triggered, Linux disposable workflow in MASTER
   with `permissions: contents: read`; do NOT change the installed PC4
   workflow or legacy composer. Pin the checked-out MASTER SHA and OPS source
   commit explicitly, and review every third-party action digest. Disable
   autoactivation, secrets, native launch, host mounts, production database and
   service access.
2. Authenticate the Git object source outside the composer's environment
   strings. The workflow's Git fetch, manifest and checkout must be anchored
   to **externally reviewed exact commit and manifest SHA256**. Never derive
   the sole trust anchor from untrusted `GITHUB_*` variables. An independently
   validated Git object hash alone is not an attestation of the runner.
3. Resolve the entire BASE tree via `git ls-tree -r` and the four EXACT
   producer diff lists `git diff --name-status --no-renames BASE SHA`. Match
   each producer's A/M path set **exactly** with the frozen reviewed allowlist,
   reject duplicate paths/statuses, symlink `120000`, gitlink `160000`,
   protected worker/launcher paths, unknown/deleted entries and conflicting
   producer changes. Get bytes using `git cat-file blob <oid>` and independently
   recompute the object and SHA256 hashes.
4. Review and construct the FINAL **whole-tree** manifest, not just a list of
   producer patch files. Include BASE regular files and the reviewed overlays.
   Resolve collisions only by an exact reviewed policy. Current prototype
   `valid_path()` restricts prefixes and therefore cannot yet represent
   BASE root files such as `pyproject.toml`; extend this with an exact
   reviewed root-file allowlist and tests BEFORE claiming full app composition.
   The manifest must be frozen with its independently reviewed SHA256.
5. Create a fresh, dedicated, same-filesystem, owner-only `0700` workspace
   **outside** both source checkout and installed runtimes. Ensure the parent
   cannot be renamed by an untrusted same-UID process; if that cannot be
   attested, stop. No producer file may ever be written into an active checkout.
6. Import only the pinned OPS private-generation module into the opt-in
   workflow. Pass exact `ReviewedBlob` values, reviewed manifest bytes,
   externally reviewed manifest SHA256, and exact source pins. Call
   `publish_generation` only in this disposable workspace. Treat errors,
   unexpected path, missing blob, receipt mismatch or unsafe inode as FAIL.
   Do NOT fall back to legacy partial per-file composition.
7. Require `inspect_recovery` state `CURRENT_VALID` and
   `read_current`/ `read_pinned` agreement on the intended generation.
   Check full tree against the frozen manifest, source checkout unchanged,
   no unknown files, and no auto-GC. An orphan, partial stage, invalid
   pointer or post-swap fsync fault means explicit diagnostics and manual
   review—not automatic selection of an older generation.
8. Run exact-composed-tree static/unit and Platform gates, isolated PG14
   **and** PG18 migration/data/provenance checks, and FastAPI/Chromium 13TF
   browser checks in a sealed, disposable read-only candidate context. No
   production PostgreSQL 5432, PC4, Windows services, trading or launcher.
9. Publish SHA-bound source manifest, full-tree receipt, hashes, negative
   test output, CI logs and full PASS/FAIL/BLOCKED matrix. A passing source-only
   or isolated composer run is **not** combined acceptance or release GO.

## Recovery protocol for MASTER reviewers

- Stop downstream use on invalid/missing CURRENT. Read-only
  `inspect_recovery` inventories valid generations and unfinished work.
- Do not auto-create a replacement CURRENT, do not delete orphan candidates,
  and do not choose a previous generation by timestamp.
- `recovery_candidate` can validate an explicit previous generation for
  operator review, but is not rollback authorization.
- Root fsync failure AFTER CURRENT replacement creates an ambiguous
  durability outcome. The visible pointer may be new; no claim is made
  about post-power-loss persistence without filesystem-specific proof.
- All generation GC and native restart remain disabled until independent
  multi-reader lease fencing and physical recovery gates exist.

## Blocking acceptance questions

`CRASH_VISIBILITY=PASS_SIMULATED`;
`RECOVERY_READ_ONLY=PASS_SIMULATED`;
`CRASH_DURABILITY=BLOCKED`;
`SAME_UID_TRUST=BLOCKED`;
`GIT_CALLER_ATTESTATION=BLOCKED`;
`GC_WITH_READER_LEASES=BLOCKED`;
`FOUR_PRODUCER_WHOLE_TREE=NOT_RUN`;
`PG14_18_BROWSER=NOT_RUN`;
`NATIVE_PC4_RESTART=BLOCKED`;
`RELEASE=NO_GO`.
