# OPS private generation composer — CI-only prototype

Scope: YehorBabenko88/tester Draft OPS PR #73 only. The source is
scripts/ops_private_generation_prototype.py. It is NOT a replacement for
MASTER #77 and must never point at PC4, installed service directories,
production PostgreSQL or an active code checkout. No launcher, trading,
UAC, reboot, force-kill or network access.

## Input authority and immutable allowlist

An external trusted controller provides ReviewedBlob entries with exact
path, bytes, SHA256, Git blob SHA1 and mode. A canonical manifest
(ops-private-generation/1) explicitly lists every file and source pin.
Its SHA256 must match an EXTERNALLY reviewed immutable digest. The prototype
recomputes both hashes and demands exact one-to-one set equality; missing,
duplicated, extra, special-mode or unsafe paths are rejected. No Git
subprocess executes here. The trusted caller must attest authentic Git
objects and reviewed commit pins; caller-supplied hashes alone are not
independent authenticity evidence.

## Generation state machine

1. Check exact immutable manifest SHA, source pins, every blob and path.
2. Verify a dedicated owner-only 0700 workspace, separate from checkout.
3. Create a fresh private staging generation; existing files are not reused.
4. Use O_CREAT, O_EXCL, O_NOFOLLOW, fchmod and fsync on each staged inode.
5. Refuse unknown files, empty surprise directories, symlinks and hardlinks.
6. Verify SHA256/Git object hash and the entire expected tree.
7. Write a VERIFIED receipt with full tree digest, fsync files/directories.
8. Re-verify the whole candidate tree immediately before publishing.
9. Rename whole candidate to a unique generation directory on one filesystem.
10. Atomically replace the regular CURRENT pointer only after verification.
11. Readers pin CURRENT and revalidate the receipt and complete file tree.

Failures before CURRENT is replaced cannot publish an incomplete generation.
The previous complete generation remains referenced after simulated late
failures. A unique generation is never replaced in place, and the source
checkout remains untouched.

## Tests and isolation

The disposable Linux CI runs tests/platform/test_ops_private_generation_prototype.py.
Tests cover owner-only 0700, manifest/blob/revision disagreement, missing or
duplicate files, traversals, symlinks, hardlinks, unknown files/directories,
in-place bytes tampering, generation corruption, CURRENT corruption/symlink,
injected write and publish faults, preservation of prior CURRENT, a hostile
directory relocation, forged CI metadata trust assumptions, and no process
launcher. No real PC4, installed Windows SCM or PostgreSQL is accessed.

## Remaining BLOCKED boundaries

- A same-UID hostile process or someone who can rename trusted parent
  directories can defeat a 0700 stage. A dedicated trusted runner, private
  mount and exclusive directory ownership must be verified outside this
  prototype. GitHub environment strings are not cryptographic attestation.
- This prototype consumes caller-supplied Git blobs but does NOT itself
  authenticate the caller or Git executable. The reviewed SHA pins and
  manifest digest must originate outside a compromised runner.
- Namespace rename and pointer replacement are atomic on the same
  filesystem. If fsync of the root fails AFTER CURRENT has changed, crash
  durability remains uncertain. Power-failure and generation-scoped recovery
  tests are necessary; this is not a production-ready durability proof.
- Readers must pin immutable generations; safe concurrent garbage collection
  of old generations is not implemented.
- Full DATA/RESEARCH/UI/OPS source composition, PG14/18, FastAPI/Chromium,
  Platform acceptance and native PC4 installation have NOT been tested with
  this prototype. Native restart and release remain NO_GO.

The protocol does not pretend an os.replace can atomically overwrite a
populated directory. It publishes a fresh unique generation and swaps only
a small verified pointer.


## Crash and concurrency follow-up

The independently tested `tests/platform/test_ops_private_generation_crash.py`
exercises crash-like exceptions immediately before/after CURRENT replacement,
root fsync after visible publication, corruption of receipt and source files,
reader pinning across switches, refused GC, late partial failures, and same-UID
writer/directory relocation at the staging-to-published boundary.

**Visibility is not crash durability.** A successful same-filesystem CURRENT
rename selects a complete tree if untrusted concurrent writes are excluded.
After a post-swap fsync exception the caller sees failure although the new
CURRENT may be visible; after actual power loss its persistence is UNKNOWN.
The tests simulate exceptions, not device power failure or journal recovery.

A same-UID writer can mutate files after the final validation, rename ancestor
folders, or defeat the trust boundary. Revalidating a pinned generation detects
corruption at read time but cannot guarantee a snapshot under live hostile
mutation. Reader pinning here means retaining the generation identifier, **not**
a durable open-fd lease. Generation deletion is unconditionally refused until
lease fencing and recovery are designed. External Git authenticity/runner
attestation, production crash durability, concurrent malicious writer isolation
and four-producer application integration remain **BLOCKED / NO_GO**.
