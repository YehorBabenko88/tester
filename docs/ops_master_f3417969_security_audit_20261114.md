# Independent OPS security re-audit — MASTER f3417969 (14 November 2026)

**Target MASTER commit:** `f341796916768b4f6b0acb1bd7d7b69cbde55155`  
**New composer Git blob:** `02a8447b26a0a6e311e70fabfa349309fc83f7e4`  
**Reviewed manifest Git blob:** `08482cd45a3f46b5b8168aab2b7b19f63acb30fb`  
**Prior MASTER commit:** `d6d6548fee441710e34f716d29adb4c25c7e4660`  
**Previous composer Git blob:** `f151161576d5e61013b3981aad255a4df247cb10`

**Method.** The workflow `.github/workflows/ops-master-v3-security-audit.yml`
checks out exact immutable MASTER v4, prior v3 and earlier v2 sources. It
verifies their Git commit and blob IDs. All adversarial filesystem mutations
occur only inside `TemporaryDirectory` through a deliberately stubbed
Git adapter. The fake Git adapter supplies a **full per-producer exact
approved manifest** rather than just one isolated changed path. No
GitHub API, Windows SCM, PC4, SSH, installed services, production PostgreSQL,
native launcher or real orders are accessed by the tests.

**Semantics of CI results:** a test called `STILL_RACE`,
`STILL_PARTIAL`, or `STILL_ACCEPTED` succeeds when it reproduces a defect.
A green unit-test workflow therefore is not a green security verdict.

## Exact comparison with d6d6548

| Threat | Before (d6d6548) | After (f3417969) | Gate |
|---|---|---|---|
| Existing hardlink to external inode | `O_TRUNC` modified external contents | O_EXCL temporary inode plus `os.replace` leaves external inode intact | **FIX CONFIRMED IN SANDBOX** |
| Approved pathname hardlinked to protected `src/api/worker.py` | Protected inode overwritten | Protected worker contents and inode preserved | **FIX CONFIRMED IN SANDBOX** |
| Worktree manifest JSON modified | Accepted extra allowed path | Compared byte-for-byte with pinned HEAD Git blob, refused | **FIX CONFIRMED** |
| Manifest symlink to attacker JSON | Followed | Worktree symlink rejected | **FIX CONFIRMED** |
| Missing or unreviewed producer changed path | Some missing entries ignored | Set of observed paths must exactly equal reviewed manifest | **FIX CONFIRMED** |
| Symlink parent/leaf and symlink swap before opening parent | Rejected | Still rejected | PASS |
| Leaf symlink planted between preflight and replace | O_NOFOLLOW blocked initial leaf open | `os.replace` replaces symlink without reading outside target | PASS for outside-file contents |
| Nonregular Git blobs, special modes, deletes, direct protected paths, traversal | Rejected | Still rejected | PASS |
| Invalid CI / Actions / GitHub SHA / OS / repo metadata | Rejected | Still rejected | PASS |
| Forged but internally consistent CI metadata with compromised Git | Accepted | Accepted | **BLOCKED** |
| Rename already-open directory outside checkout | Write escapes checkout | Write still follows renamed directory descriptor | **BLOCKED** |
| Failure in a late replacement after earlier writes | Partial composition | Partial composition still remains | **BLOCKED** |
| Attacker replaces newly written temporary source inode with symlink before `os.replace` | Different prior failure mode | Can plant final symlink in output tree | **BLOCKED (new race)** |
| Unexpected pre-existing workspace file | Retained | Retained | **UNVERIFIED / checkout hygiene** |
| Duplicate Git diff entries | Not checked strictly | Set equality collapses duplicate entries | **policy gap** |
| Git adapter reports exact HEAD/blob ID but supplies unrelated file bytes | Requires compromised Git reader | Composer accepts bytes from Git show without independent object ID verification | **trust-boundary limitation** |
| Native launcher / installed Windows service activation | No direct call | No direct call | Source + sandbox PASS; physical PC4 still NOT_RUN |

## Key remaining issues

1. **Relocated directory descriptor:** A process with write access to
   checkout parents can rename an already opened approved directory into
   another part of the filesystem before `os.replace`; the destination
   `dir_fd` then points outside the trusted root. `O_NOFOLLOW` cannot
   prevent relocation of a *legitimate* directory inode.
2. **Temporary source substitution:** A process with write access to the
   approved output directory can watch/replace the temporary inode after
   `os.fdopen` closes it and before the final `os.replace`. The result can
   be a symlink in the composed output (outside target contents need not be
   modified). Verifying the final output's regular-file mode, source SHA and
   inode identity is necessary, but still does not solve arbitrary concurrent
   filesystem tampering; private staging must deny other local writers.
3. **Not transactionally atomic:** The script applies files sequentially.
   A late disk error can leave earlier approved files changed. Preflight
   success does not imply a valid complete candidate, and no rollback,
   commit marker or full-tree manifest gate exists.
4. **Runner/Git trust:** The metadata environment strings and mock Git
   can be forged by a malicious process. Real Git object integrity helps
   when Git itself and the checked-out commit are trusted; there is no
   cryptographic attestation of a generic runner solely from environment
   variables. This is a conditional threat, not a claimed exploit of
   GitHub's actual hosted infrastructure.
5. **Manifest/diff exact-set semantics:** The trusted manifest matches the
   producer diff *as sets*, not multiplicity-aware sequences. Duplicate
   diff entries can remain accepted; the approved manifest itself may
   contain duplicated names because it is converted to a set.
6. **Unexpected worktree files:** Files outside the approved producer diff
   may already exist in the checkout and are not detected/removed. They
   could affect downstream test import paths when not in a clean private
   checkout.

**Recommended MASTER-owned changes:** use a fresh private (0700) candidate
directory with exclusive write ownership and no untrusted concurrent
rename permissions; finish composition without modifying the active
checkout; write and fsync each regular inode, ensure mode/digest exactly
match the pinned Git blob and no unexpected files; publish a completed
candidate only via a single verified atomic directory-level handoff;
reject duplicate/unexpected Git diff entries; record a final immutable
composition receipt and forbid acceptance of partial output. Continue
trust review of the hosted runner and Git executable separately.
If owner cannot prove a race-free workspace, fail closed.

## Independent source-only / physical boundary

The exact composer only retrieves source through Git subprocesses,
writes disposable files, prints a manifest, and does not call native
Windows launcher or Windows SCM commands. Unit tests patch process spawn
functions in the isolated harness. These findings **do not imply**
that actual PC4 files, services, or PostgreSQL were accessed or changed.
Native installed PC4 restart remains **BLOCKED / NOT_RUN**.

Full release **NO_GO** until all security issues are reviewed and one
immutable combined source SHA passes required PostgreSQL 14/18,
FastAPI/Chromium, DATA/RESEARCH/UI, OPS, and Platform acceptance gates.

The OPS owner may amend only Draft PR #73. MASTER #77 and main must remain
unchanged.
