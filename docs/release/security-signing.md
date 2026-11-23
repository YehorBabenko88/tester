# Release trust, owner signing and irreversible removal

No production public key or production private key is supplied by this repository.
An unsigned package is **NO_GO**. Ephemeral keys in tests are not production trust.

## Signed package contract

`release-manifest.json` is canonical UTF-8 JSON (sorted keys, compact separators,
ASCII escapes, no NaN), containing exactly `manifest`, `public_key_hex` and
`signature_hex`. The signed payload contains `schema: tester-release/v1`,
`release_version: 1` (a string), the exact 40-character `source_sha`, repository
`https://github.com/YehorBabenko88/tester`, and `files` mapping every payload file's
relative POSIX name to SHA256. The envelope excludes itself from that inventory.
A component manifest, SBOM, dependency archives and install scripts are payload
files and must be included. Unknown files, missing files, case collisions,
traversal, alternate data streams, reparse points and hardlinked files fail closed.

Ed25519 uses the existing `windows_release_signature.verify_signed_manifest`
implementation and its `TESTER-INSTALL-MANIFEST-v1\0` domain separation. The
trusted fingerprint is SHA256 of the raw 32-byte public key. It must arrive via
an independently authenticated owner channel, never from the package itself.
The expected commit is also independently pinned. Signing attests the owner's
provenance review; a Git SHA string alone does not establish GitHub provenance.

## Exact owner action (not executed by Codex)

On the owner's offline signing workstation, create or select an encrypted
Ed25519 PKCS8 PEM key outside this repository and outside the payload directory.
Review the exact source commit, complete inventory, dependency locks, SBOM and
release evidence. Then, using a separately verified checkout/runtime:

```powershell
python -m src.api.release_security sign --bundle D:\Release\payload --private-key D:\OwnerKeys\tester-release.pk8.pem --source-sha <FINAL_40_HEX_SHA> --manifest-input D:\Release\release-manifest.unsigned.json
```

The command prompts for the key passphrase, never writes the key, creates the
canonical envelope without overwriting any existing signature, and prints the
public-key fingerprint and manifest digest. The unsigned manifest is optional;
when supplied it must exactly match the actual payload. The owner must approve
and distribute that fingerprint through the independent channel. Never replace
it with a generated test key. Reverify before installation:

```powershell
python -m src.api.release_security verify --bundle D:\Release\payload --trusted-public-key-sha256 <OWNER_APPROVED_64_HEX> --source-sha <FINAL_40_HEX_SHA>
```

The verifier returns immutable `VerifiedRelease` inventory metadata. Consumers
must verify the actual staged bytes immediately before executing/installing them;
a cached receipt or `signed_bundle_verified=true` is not an authentication gate.

## Ownership and deletion contract

`write_ownership` accepts explicit installer **creation receipts**, never an
unqualified filesystem snapshot. The external administrator-protected ledger is
pinned by its own independently stored SHA256. It binds the installation UUID,
canonical absolute root, immutable marker, signed release envelope, full
immutable release inventory, and native volume/file identity of every registered
file and directory. Its release prefix is normally `releases/<source_sha>`.
Mutable registered files retain identity protection; their content can change.
The ledger is not a backup: it contains metadata, never user-data bytes.

Removal displays all registered paths and SCM scope, warns about irreversible
loss, and requires the exact phrases `DELETE TESTER` and `YES`. The single native
SCM adapter from `release_service` owns service/shortcut/task/autostart checks;
it must validate UUID, ImagePath/executable, cluster identity and process exit,
and reject foreign resources. The shared `release_install.lifecycle_lock` must
be outside the removed root. No second SCM manager, PostgreSQL client, process
killer or recursive shell deletion is implemented here.

The deletion engine validates all current entries, stops/drains through that
adapter, then pins every ancestor, directory and file using native Windows
handles that refuse concurrent write/delete sharing. It rechecks identity and
immutable content through those handles, revalidates resources, removes owned
external resources, and deletes only the verified handles from leaves to root.
New foreign entries are preserved and cause directory removal to fail. Locked
files fail closed. Partial deletion retains external fsynced append-only JSONL
evidence with nonsecret status/count/error type. Repeated attempts may remove
only remaining identities from the original pinned ledger; use a new evidence
filename after a partial attempt. Already completed removal is idempotent.

Run the wrapper from independently verified external media/runtime:

```powershell
.\Uninstall-ReleaseTester.ps1 -Root C:\TesterLab -Ledger <EXTERNAL_PROTECTED_LEDGER> -TrustedLedgerSha256 <64_HEX> -TrustedPublicKeySha256 <OWNER_64_HEX> -SourceSha <40_HEX> -EvidencePath <NEW_EXTERNAL_JSONL> -ModuleSource <VERIFIED_EXTERNAL_SOURCE> -Python <EXTERNAL_PYTHON_EXE>
```

An interpreter or working directory inside the deleted root cannot remove itself
safely and is refused. The wrapper preserves `GUARDIANS_ENABLED=false`. Do not
place deleted user-data backups in maintenance media.

## Acceptance limits and open release gates

The new tests execute real handle-based deletion only on fresh unique temporary
installations containing authorized dummy files, with foreign sentinels. They do
not touch C:\TesterLab, ADMINIT, any existing services or PostgreSQL 5432. Native
SCM/service/PG lifecycle acceptance belongs to the integrated Windows acceptance
run. Native deletion is Windows-only; Linux deletion is explicitly refused.

The host token cannot create symbolic links (WinError 1314), so two real symlink
tests report SKIP with that reason; this is **NOT RUN**, not PASS. Real Windows
junction, hardlink, directory/root swap, immutable tamper, locked file, late foreign
creation, durable partial failure and retry tests execute independently.

**Runtime ownership remains a release gate:** new PostgreSQL WAL/log/cluster,
worker/history/cache/result files must be registered by authenticated creators in
a durable protected ownership journal. A post-hoc directory snapshot, shared
LocalService account or location under an owned directory does not prove file
ownership. Until creator provenance is integrated, unknown runtime files cause
safe refusal; a complete populated production uninstall is not claimed. The
external maintenance trust anchor and shortcut/task/autostart lifecycle also
require integrated acceptance. Production key approval and independent Guardian
GO remain required before publication.
