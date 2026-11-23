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
files and must be included. Unknown bundle files, missing bundle files, case collisions,
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

Run the installed protected wrapper using its administrator-protected external trust config:

```powershell
.\Uninstall-ReleaseTester.ps1 -TrustConfig <INSTALLATION_EXTERNAL_TRUST_JSON>
```

The original downloaded installer is **not required**. The pinned ownership ledger
contains the signed envelope, explicit signed-bundle-to-installed-file mapping,
and SHA256 of derived immutable dependencies created by the trusted installer
from verified wheel archives. All signed source/Python/PostgreSQL runtime files
must map to installed bytes; wheelhouse archives may remain outside installation.

Before executing any private Python or module code, the protected PowerShell
launcher reads the independently protected trust config and ledger digest. It
pins source ancestors/directories/files with native no-share-write/delete
handles and verifies every maintenance source SHA256. It copies only immutable
source, private Python and derived site-packages into a fresh UUID temporary
child of the protected external metadata directory. Mutable user data, history,
results, cluster files, config secrets and backups are never staged. The trusted
launcher runs the staged private interpreter with bytecode disabled; no original
media or persistent duplicate runtime is needed.

After releasing source locks, the staged Python verifies the existing Ed25519
proof and performs removal. Its durable status is
`root_removed_pending_maintenance_cleanup`, not full completion. After Python
exits, the PowerShell controller deletes only the native identities from its
fsynced creation journal, using the same handle-disposition primitive as the
Python deletion engine. Unknown files or changed identities preserve the object
and fail closed. Only after transient cleanup and TrustConfig removal does the
controller append final `removed` evidence. The metadata-only ownership ledger
and external nonsecret JSONL removal evidence remain for audit and recovery.
A durable protected maintenance creation journal permits retry of partially
removed transient files, including when the installation root is already gone;
root absence alone never substitutes for native-removal completion evidence.

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

Dynamic data ownership uses authenticated OS writer boundaries, rather than a
post-hoc filesystem snapshot. The pinned ledger binds explicit installer-created
mutable directory identities to unique `NT SERVICE\<UUID-owned-service>` virtual
accounts. Boundary ACLs must be protected and grant exactly SYSTEM/Administrators
full control plus that unique service SID modify access. All descendants must
have the exact inherited ACL; newly created or recreated mutable files must have
the unique virtual service account as their kernel owner SID. Shared LocalService
ownership, foreign creator SIDs, unknown ACLs, explicit descendant grants,
hardlinks and reparse points are rejected. Existing explicit bootstrap receipts
remain identity-bound; immutable source and boundary roots cannot be replaced.
All account writers must stop before final security/identity checks through
locked native handles. Administrator/SYSTEM tampering is outside this boundary,
as it is for the independently protected external trust pin itself.

**Remaining acceptance gates:** actual elevated native SCM/virtual-account file
creation and full populated lifecycle must pass in the integrated Windows run.
Policy evaluator tests and real foreign-owner rejection are not proof of a
successful virtual-account PostgreSQL lifecycle. The local host lacks symlink
creation privilege; both symlink cases remain NOT RUN here. Production key
approval and independent Guardian GO remain required before publication.
