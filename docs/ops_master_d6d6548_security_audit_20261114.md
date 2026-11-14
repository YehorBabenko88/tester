# OPS independent security audit — MASTER d6d6548 (2026-11-14)

**Audited MASTER immutable commit:** `d6d6548fee441710e34f716d29adb4c25c7e4660`  
**Composer Git blob:** `f151161576d5e61013b3981aad255a4df247cb10`  
**Reviewed allowlist Git blob:** `08482cd45a3f46b5b8168aab2b7b19f63acb30fb`  
**Previous MASTER:** `88ad7a7a82f35d1829b5eb07c0c1d4878741f527`  
**Frozen producer pins, unchanged:** DATA `d5bfdad4`, RESEARCH `7ec35810`, UI `a61a2c3b`, OPS `0d4649f3`.  
**Environment:** hosted Linux CI only, with isolated `TemporaryDirectory`,
real source/manifest at exact Git SHA, mocked Git responses and no network,
installed Windows, services or production PostgreSQL.

## Exact observed negative results

In [OPS MASTER v3 audit run 43587519237](https://github.com/YehorBabenko88/tester/actions/runs/43587519237),
the **previous v2 sandbox regressions 8/8 PASS** and the
**exact v3 sandbox attack matrix 20/20 PASS**. *A PASS for a test named
`VULNERABILITY` proves the unsafe behavior is reproducible*, **not**
that the composer is secure.

| Threat | Observed at d6d6548 | Release gate |
|---|---|---|
| Normal approved regular blob, no native API/process invocation | PASS, sandbox-only | Retain |
| Pre-existing symlink parent and leaf | Refused before writes (PASS) | Retain |
| Symlink parent/leaf swap immediately before descriptor open | `O_NOFOLLOW` refused (PASS) | Retain |
| Git symlink, gitlink, special mode | Refused (PASS) | Retain |
| Traversal, deletion, direct protected PC4/worker path, unlisted source | Refused (PASS) | Retain |
| CI/GITHUB_ACTIONS/GITHUB_SHA/repo/run-id/OS/workspace mismatch | Refused (PASS) | Retain |
| Preflight scans all destinations before any writes | Refused invalid second destination, first untouched (PASS) | Retain |
| Forged matching CI/Actions/GitHub metadata in user-controlled environment + mocked Git | **Accepted** (security FAIL) | BLOCKED: not cryptographic runner attestation |
| Hardlink alias from allowlisted leaf to writable outside-checkout inode | **Truncates outside file** (security FAIL) | BLOCKED |
| Hardlink alias from allowlisted leaf to protected `src/api/worker.py` | **Overwrites protected worker inode** (security FAIL) | BLOCKED |
| Parent directory moved outside checkout after its original `dir_fd` acquired | **Writes into relocated directory** (security FAIL) | BLOCKED |
| Working-tree allowlist extended with unreviewed installer filename (mocked malicious producer diff) | **Accepted** (security FAIL) | BLOCKED; immutably pin manifest |
| Worktree allowlist symlink points to external JSON | **Accepted** (security FAIL) | BLOCKED |
| An unrelated reviewed entry removed from allowlist | **Undetected** if not in current diff (security FAIL) | BLOCKED; exact expected-set validation |
| Unexpected pre-existing workspace file | **Retained**; not rejected (policy gap) | Review checkout hygiene |
| Later write fails after earlier approved write | **Partial composition remains** (security FAIL) | BLOCKED; no atomic staging/rollback |
| Native launcher activation / Windows SCM / PC4 | No activation in exact source under patched `Popen`/`run`/`system`, **PASS in sandbox only** | Physical PC4 remains NOT_RUN/BLOCKED |

The exact reviewed allowlist contains DATA 21, RESEARCH 15, UI 37
and OPS 65 file names. It is an *allow-set*, not a verified equality
between every reviewed expected path and the exact producer diff.

## Specific vulnerabilities and limitations

1. **Existing-file hardlinks are not symlinks.** `os.open(O_TRUNC |
   O_NOFOLLOW)` truncates the shared inode even when a distinct allowed
   pathname links to an outside file or protected worker. Merely checking
   `target.is_file()`, source mode or `O_NOFOLLOW` cannot prevent this.
   No actual PC4 file or service was modified; a malicious co-located
   writer or pre-created hardlink is a prerequisite to exploiting this.
2. **Directory relocation race.** `dir_fd` securely resists symlink swaps
   but it can refer to a directory that another actor has since renamed
   outside the checkout. Tests reproduce such a late rename while
   preserving the descriptor. The script cannot claim strict path
   containment under a hostile process with rename permissions.
3. **Unpinned worktree manifest.** The composer reads
   `ROOT/docs/coordination/master-reviewed-producer-files.json`
   directly via `read_text()`, with no `lstat`, no Git-blob SHA
   comparison, and no equality check against producer diff sets. A
   compromised/mutated checkout can extend or omit allowlist entries.
   Fixed genuine Git producer SHA and a trusted clean checkout greatly
   reduce exposure, but these are external trust assumptions.
4. **Runner identity.** The presence of
   `CI/GITHUB_ACTIONS/GITHUB_SHA/GITHUB_REPOSITORY/GITHUB_RUN_ID/RUNNER_OS`
   is not independent authentication of the Actions environment. The
   sandbox reproduces acceptance of forged *matching* metadata with a
   compromised Git reader. The genuine job's immutable checkout and
   token permissions must be independently trusted.
5. **No all-or-nothing output.** After successful preflight, later
   filesystem errors leave already written source files in-place; the
   composer cannot roll back. Such a partially composed tree must not be
   accepted or reused by any downstream test or installer.
6. **Native/PC4 boundary.** This tool calls Git subprocesses for source
   fetching and `os.open` for files; no Python subprocess invocation of
   native launchers, SCM, UAC, `kill`, reboot or SSH was observed in
   source/isolated tests. However this **does not** prove an untrusted
   runner or filesystem can never affect external machines. No PC4 or
   Windows installation was contacted.

## Remediation required before release / MASTER integration authorization

- Fetch reviewed manifest from the **exact trusted staging commit object**
  (`git show SHA:path`), validate its canonical SHA/hash and expected
  schema/key set, reject worktree symlinks and any extra/missing or
  duplicate producer file, and require exact diff-set equality.
- Never `O_TRUNC` an existing checkout inode. Prefer a **fresh private
  staging directory** with strictly controlled write permissions,
  create-only temporary regular files and descriptor-relative atomic
  replacement. Refuse hardlinked destinations (`st_nlink > 1`) and
  verify parent device/inode identity where meaningful. Treat any
  external directory relocation or untrusted concurrent writer as
  **BLOCKED** instead of pretending `O_NOFOLLOW` alone solves it.
- Fail on all exceptions with an explicitly rejected generation;
  create final artifacts only after complete, independently verified
  composition. Do not run launchers, installations, or downstream test
  acceptance against a partially written tree.
- Keep source checkout immutable, trusted runner boundaries and
  permissions minimal. A status flag by itself is not cryptographic
  attestation. Pin Git executable/toolchain and any token/checkout trust.
- Continue all requirements on a **single committed combined SHA**
  (actual PG14/18, Research/Strategy F, FastAPI/Chromium, security, OPS)
  only after the composer is repaired and reviewed by its MASTER owner.

## CI comparison and release status

The previous [88ad7a7 run](https://github.com/YehorBabenko88/tester/actions/runs/43584268898)
and new [d6d6548 run](https://github.com/YehorBabenko88/tester/actions/runs/43586419054)
both passed *source composition* (125 files, 325 Python AST), but neither
constitutes adversarial filesystem containment proof.

The OPS source branch also passed [isolated OPS run 43587519295](https://github.com/YehorBabenko88/tester/actions/runs/43587519295)
for all nine jobs including Windows test fixtures, disposable PG16 and
read-only compatibility. **This does not override demonstrated MASTER
composer security FAILs.**

**Security disposition: BLOCKED / NO_GO**. No modification of MASTER #77,
main, real PC4, production PostgreSQL 5432, no Codex, force-kill, UAC or
reboot. The negative tests are isolated *evidence*, not a fix in MASTER.
