# OPS security audit — exact MASTER 5ee448fa (14 November 2026)

**Audited SHA:** `5ee448fa6e0c356034e33b6f54f2ddbf6bdbc1cd`  
**Composer blob:** `c107f861144b9ef5adcc41eb8460fb5c9178e119`  
**Reviewed manifest blob:** `08482cd45a3f46b5b8168aab2b7b19f63acb30fb`  
**Previous version:** `f341796916768b4f6b0acb1bd7d7b69cbde55155`, blob `02a8447b26a0a6e311e70fabfa349309fc83f7e4`.

## Tests and interpretation

New `tests/platform/test_ops_master_composer_v5_security.py` loads exactly
the audited source, substitutes its Git reader with a deterministic mock of
the **complete per-producer manifest and diff** and confines writes to
`TemporaryDirectory`. The workflow also runs prior v2 (8 tests),
v3 (20) and v4 (21) for historical comparison.

**A passing test named STILL_RACE / STILL_PARTIAL / STILL_ACCEPTED
demonstrates an unsafe behavior; it does not constitute a security PASS.**

| Boundary | MASTER f3417969 | MASTER 5ee448fa | Security result |
|---|---|---|---|
| Duplicate producer diff paths | accepted (set equality) | explicitly rejected | FIXED in sandbox |
| Temporary inode changed before lstat | not detected | inode/dev/mode/nlink check rejects | FIXED in sandbox |
| Existing hardlink to outside/protected worker | original preserved | preserved | PASS retained |
| Worktree manifest mutated/symlinked | rejected | rejected | PASS retained |
| Extra/missing approved producer file | rejected | rejected | PASS retained |
| Direct protected paths / deletion / traversal / special Git modes | rejected | rejected | PASS retained |
| Parent/leaf symlink and pre-open symlink swaps | rejected | rejected | PASS retained |
| Temporary inode swapped **after** lstat, immediately before replace | final output symlink | final output symlink | **BLOCKED** |
| Temporary inode content changed after lstat, same inode | not hash-verified | still not hash-verified | **BLOCKED** |
| Rename parent directory after dir_fd open | out-of-checkout write | remains possible | **BLOCKED** |
| Late failure after earlier successful replacements | partial composed tree | remains partial | **BLOCKED** |
| Forged coherent runner metadata and untrusted mocked Git | accepted | accepted | **BLOCKED (conditional trust assumption)** |
| Unapproved pre-existing files in checkout | retained | retained | **workspace hygiene gap** |
| Windows native launcher / SCM commands | no direct activation | no direct activation | Source/sandbox PASS only |

The new `os.stat(tmpname, follow_symlinks=False)` observes one instant.
A malicious process with write access to the staging parent may replace
the checked inode immediately after that observation, before
`os.replace`, or mutate bytes while keeping inode identity unchanged.
A *private directory with exclusive access* is the primary security
boundary; adding more successive stat checks alone cannot close this
window under a hostile concurrent writer.

## Proposed private staging and whole-tree atomic publication

This proposal belongs to the MASTER code owner. No implementation is
allowed in MASTER #77 by this OPS audit.

1. **Trusted inputs and source freeze.** Require a pinned staging commit,
   exact producer commit SHAs, immutable committed allowlist, and trusted
   Git object reader. Validate the full per-producer diff as a
   multiplicity-aware sequence: no duplicates, no missing/extra/renamed/
   deleted paths, no special Git modes. Hash each retrieved blob, compare
   with the tree blob object and expected receipt. Refuse arbitrary
   substituted Git content and mismatched metadata before filesystem work.
2. **Own a private same-filesystem staging parent.** On isolated hosted
   Linux, create an owner-only `0700` directory under an approved dedicated
   CI temporary mount, with `umask 077`, no symlink ancestry, no inherited
   write rights and no untrusted co-located processes. Explicitly verify
   directory identity and mount; if other writers or namespace moves
   cannot be excluded, **BLOCKED**. Prefer a private mount namespace
   when available and authorized; never imply environment strings are a
   cryptographic attestation.
3. **Build entirely outside the checked-out producer tree.** Materialize
   the exact PC4 base into `stage/<generation>/candidate` in this private
   area and overlay each authorized producer file inside that tree.
   Do not `O_TRUNC` existing inodes in the source checkout; create
   `O_CREAT|O_EXCL|O_NOFOLLOW` regular temporary files, write exact bytes,
   `fsync`, and atomically replace *inside the private candidate only*.
   Never execute scripts, native launchers or post-install hooks during
   copying.
4. **Verify finished candidate.** Walk via directory FDs without
   following symlinks. Compare the complete resulting file-tree manifest
   against the computed expected tree, including SHA256, Git blob id,
   file mode, allowed paths and absence of unexpected files. Refuse
   symlinks, hardlink escape, duplicate entries, missing or changed
   protected paths and unexpected executables. Recheck the candidate
   identity and prevent writes after verification (private rights +
   generation-scoped ownership and freezing); do not accept a
   checksum-only scan while another writer can race it.
5. **Emit a sealed completion receipt.** Write a canonical
   `generation.json` with {source SHA pins, reviewed manifest digest,
   expected tree digest, job/run identity, bounds, status:"VERIFIED"}.
   `fsync` the receipt and relevant parent directories. A failed or
   incomplete candidate has status FAILED/QUARANTINED and cannot be
   promoted. No fallback to stale candidate or unverified files.
6. **Publish the entire generation, not each file.** Keep staging and
   published generations on the same filesystem. Once verified,
   `rename` the complete private `candidate` directory into a
   previously nonexistent, unique generation path using an
   exclusive-create/no-overwrite protocol. Make this generation immutable
   to the consumer. Only then atomically replace a small `CURRENT`
   pointer/receipt (regular file, not a followed symlink) referencing
   the verified generation digest. Consumers pin the generation and
   verify the receipt; they never look at an in-progress stage.
   A single same-filesystem directory rename or pointer swap yields
   a coherent view, but does **not** atomically overwrite a populated
   destination directory with `os.replace`. Handle that explicitly.
7. **Rollback and fault injection.** On any write, fsync, hash,
   directory-rename or publish fault: do not change `CURRENT`, mark
   the generation FAILED and quarantine/clean only that disposable
   stage. Tests must inject failures before/after each boundary,
   simulate duplicate/missing/out-of-policy sources, hardlinks,
   symlinks, rename races, and aborted process recovery. Never infer
   success from the existence of a directory without a verified
   completion receipt.
8. **CI and authorization gates.** Perform testing on the **exact
   committed combined source SHA** through isolated PostgreSQL14/18,
   FastAPI/Chromium, RESEARCH/F provenance, OPS and security suites.
   Source composition **must not** activate native services, UAC,
   reboots, force kills or trading. PC4 physical restart remains
   separately authorized and BLOCKED.

### Required negative tests for the architecture

- Simulate a hostile writer replacing a temporary file before/after
  stat and during/after hash verification: no published generation.
- Move a parent between path checks or before atomic publication:
  publication must be refused unless exclusive ownership rules are
  independently enforced.
- Inject EIO/ENOSPC at every source write, fsync, hash, rename and
  CURRENT pointer swap; assert the previously published generation
  remains byte-identical and available and no partial generation
  becomes current.
- Insert unexpected pre-existing files, symlinks and hardlinks:
  full manifest verification must fail.
- Simulate duplicate/missing/different producer file status and
  changed Git/manifest responses: reject before any staging writes.
- Forged `CI/GITHUB_ACTIONS/GITHUB_SHA` strings without trusted
  immutable source/run binding must be denied; no assertion of
  unforgeability from strings alone.

## Verdict and environment boundaries

**Current MASTER 5ee448fa source hardening is improved, NOT
security-accepted.** The requirement is exclusive private stage and
whole-generation atomic publishing; a per-leaf `os.replace` does not
satisfy all-or-nothing release atomicity.

Audits do not contact installed PC4, Windows services, PostgreSQL5432,
SSH, or any trading backend. The test process uses only mock Git data,
local temporary directories and pinned composer source. Full release
remains NO_GO until the new design is reviewed and accepted by MASTER
and all required combined-tree CI gates pass.
