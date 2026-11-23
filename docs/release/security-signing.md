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

## Interrupted fresh-install rollback

`rollback_provisioning(root, journal_path=..., trusted_public_key_sha256=...,
expected_source_sha=...)` is restricted to an authenticated fresh provisioning
transaction that has never exposed its normal API runtime and has never recorded `ready`, `completed` or `committed`. It is
not an alternate user uninstaller and does not adopt an existing installation.
The external `tester-provisioning/v1` JSONL header is created and fsynced under
protected SYSTEM/Administrators ownership before the root exists. It binds the
UUID marker, independently verified signed release envelope, parent identity,
unique provisioning App service SID, and exact actor output scope.

Initial copy plans are fsynced before native creation. Native SECURITY_ATTRIBUTES
assign the unique App SID as owner **at creation**, with a protected SYSTEM/Admin
DACL. This closes the create-before-file-ID-receipt crash gap: an unreceipted
planned temporary object still requires the unique kernel owner and restrictive
ACL; an Admin/user-owned object at the same planned path is refused. Created
native IDs, signed source hashes, atomic rename identities and finalized hashes
are recorded before ownership changes. A rename that completed before its journal
record can be recovered only through its already recorded temporary file ID and
exact planned final hash. Malformed complete journal records fail closed. A torn
last append permits rollback of the authenticated prefix but cannot be resealed.

`seal_provisioning_outputs` stops and proves the existing App provisioning actor
through the shared native service/job adapter, authenticates newly created outputs
by unique owner SID and absence of foreign ACL identities, then fsyncs their IDs
and derived venv code hashes to the protected journal. This occurs before changing
venv/cluster owners or ACLs. Unknown Admin files are never enrolled. Both helpers
reuse the existing native handle locking and handle-disposition deletion engine;
no additional service, supervisor, process killer or PostgreSQL client is added.
Callers already holding the installer lifecycle lock pass a null context callback
rather than acquiring the same operation lock twice, and close their writable
journal handle before calling either helper.

The journal can declare exactly the two Desktop shortcuts. Their plans precede
native unique-owner creation; recorded IDs and finalized hashes bind COM output.
Other Desktop files and replaced shortcut identities are preserved. A
`final_ledger_sealed` event made before normal services start routes subsequent
fresh-install failure through the already authenticated final ownership ledger
and its separate App/PostgreSQL writer boundaries. The final marker, source,
UUID, root identity and trust-config scope must match the provisioning header.
Automatic rollback authorization applies only to this never-committed fresh
transaction; a completed installation requires normal irreversible confirmations.

Rollback keeps an external append-only nonsecret removal receipt and the protected
provisioning journal. Missing already removed identities are tolerated on retry;
unknown entries, replaced IDs, foreign creator SIDs, reparse points, hardlinks and
locked objects refuse deletion. Native temporary-fixture tests exercise real
handle deletion and partial retries; the non-elevated local host isolates only
the external Admin-ACL enrollment gate. Full elevated virtual-account provisioning,
COM interruption and populated PostgreSQL acceptance remain integrated CI gates.

The installer must fsync `{event: "runtime_exposed"}` **before** the first normal
API/app service start, including readiness probing. This event permanently bans
automatic provisioning rollback, even if readiness later fails and `completed`
was never recorded. Once API users could have submitted work, retain the root for
validated resume or the normal `DELETE TESTER` / `YES` uninstall. A provisioning
actor that never exposes the API remains eligible for authenticated rollback.

## Windows bootstrap trust and AllSigned production gate

Ed25519 release-manifest verification is **not Authenticode**, does not sign a
PowerShell script for Windows, and cannot authenticate code that has already
started as Administrator. An unsigned `Setup-Tester.ps1` remains production
**NO_GO**, even when a manually supplied envelope SHA256 is correct. The release
also requires an owner-approved code-signing certificate, its independently
published SHA256 of raw certificate DER, a trusted CA chain, and the approved
publisher in the operator's Windows trust policy. No owner certificate, private
key or trust-store change has been supplied or performed by Codex.

PowerShell AllSigned checks script signatures from trusted publishers; the
separate manifest verifier then checks the externally pinned Ed25519 key and
payload inventory. These are two different trust roots. See Microsoft's
[about_Signing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_signing?view=powershell-7.4).

### Owner production signing order (not executed)

Perform this on the separately trusted owner signing workstation, using an
independently reviewed source checkout and external approved component inventory.
Select the actual CA-issued code-signing certificate from the owner's certificate
store/HSM. Do not create a self-signed production substitute or export its private
key into the package. Obtain all placeholders below through the owner channel.
The store thumbprint is only a certificate locator; SHA256 of raw DER is the
independent identity pin.

```powershell
$Bundle = 'D:\Release\payload'
$ApprovedUnsignedManifest = 'D:\Release\payload.unsigned.json'
$OwnerStoreThumbprint = '<CERTIFICATE_STORE_LOCATOR>'
$OwnerCertificateSha256 = '<OWNER_APPROVED_64_HEX_DER_SHA256>'
$TimestampServer = 'http://<OWNER_APPROVED_TIMESTAMP_SERVER>'
$certs = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Thumbprint -eq $OwnerStoreThumbprint })
if ($certs.Count -ne 1 -or -not $certs[0].HasPrivateKey) { throw 'Approved signing key unavailable' }
$cert = $certs[0]
$sha = [Security.Cryptography.SHA256]::Create()
try { $certHash = [BitConverter]::ToString($sha.ComputeHash($cert.RawData)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
if ($certHash -cne $OwnerCertificateSha256) { throw 'Independent publisher identity mismatch' }
$original = Get-Content -LiteralPath $ApprovedUnsignedManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$rows = @()
foreach ($relative in @('Setup-Tester.ps1','payload/scripts/windows/Start-ReleaseTester.ps1','payload/scripts/windows/Uninstall-ReleaseTester.ps1')) {
    $path = Join-Path $Bundle $relative
    $before = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($before -cne $original.files.PSObject.Properties[$relative].Value) { throw 'Unsigned source artifact changed before owner signing' }
    $signature = Set-AuthenticodeSignature -LiteralPath $path -Certificate $cert -HashAlgorithm SHA256 -IncludeChain NotRoot -TimestampServer $TimestampServer
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.TimeStamperCertificate) { throw 'Authenticode signing/timestamp acceptance failed' }
    $rows += [ordered]@{path=$relative;source_sha256=$before;signed_sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();certificate_sha256=$certHash}
}
$receipt = [ordered]@{schema='tester-script-signatures/v1';source_sha=$original.source_sha;scripts=$rows}
[IO.File]::WriteAllText((Join-Path $Bundle 'script-signing-receipt.json'),($receipt | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
```

This signs the three canonical executable entry scripts. Vendor `.ps1` templates
inside immutable runtime components are inert data; they are not alternate TESTER
entry points and must not be invoked as unsigned bypasses. Any additional script
that becomes executable in a supported user flow needs an approved signature and
updated acceptance. Signing adds bytes to the entry scripts, so the builder's
old unsigned manifest is now stale. Preserve the pre/post script receipt and the
original source-evidence archive; the logical Git source SHA remains the reviewed
commit, while the signed artifacts have separately recorded byte hashes.

`Set-AuthenticodeSignature` supports SHA256 and an external timestamp authority;
Windows PowerShell 5.1 uses an HTTP timestamp URL. Require a valid returned
signature and an actual timestamp rather than assuming a successful invocation.
See Microsoft's [Set-AuthenticodeSignature documentation](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-authenticodesignature?view=powershell-5.1).

Recompute the complete post-Authenticode inventory using the prepared mechanism,
review it outside the payload, and only then apply the separate Ed25519 signature:

```powershell
python -m src.api.release_security manifest --bundle D:\Release\payload --source-sha <FINAL_40_HEX_SHA> --output D:\Release\post-authenticode.unsigned.json
python -m src.api.release_security sign --bundle D:\Release\payload --private-key D:\OwnerKeys\tester-release.pk8.pem --source-sha <FINAL_40_HEX_SHA> --manifest-input D:\Release\post-authenticode.unsigned.json
python -m src.api.release_security verify --bundle D:\Release\payload --trusted-public-key-sha256 <OWNER_ED25519_64_HEX> --source-sha <FINAL_40_HEX_SHA>
```

Finally build the deterministic ZIP from those finalized signed bytes and publish
its SHA256, Setup SHA256, envelope SHA256, raw code-signing certificate SHA256,
Ed25519 fingerprint and source SHA through the independently authenticated owner
channel. Signing/TSA operations can change signature bytes between attempts;
reproducibility claims must identify the exact signed inputs, not imply that two
fresh timestamp/signing operations produce identical signatures.

### Operator bootstrap before any package code executes

Use trusted Windows console commands, not a helper copied from the unverified
package. The operator account, OS trust store and these commands are the bootstrap
root of trust. Create a fresh UUID staging tree with SYSTEM/Administrators-only
ACLs and Administrators ownership **before** copying package bytes into it. Copy
the downloaded ZIP there first, protect the destination object, and verify the
independent ZIP SHA256 on that protected copy. A mutable Downloads file is never
executed; any swap or mutation during copying must produce a digest mismatch.
Extract only into that protected staging tree; reject reparse points, unsafe ZIP
paths and unexpected entries, and protect all extracted directory/file owners and
ACLs before verification. Do not follow a link while applying protection.

The trusted operator then performs these checks and invocation against protected
paths, filling all pins from the owner channel rather than the package itself:

```powershell
$Setup = '<PROTECTED_STAGING>\bundle\Setup-Tester.ps1'
$ExpectedSetupSha256 = '<OWNER_SETUP_64_HEX>'
$ExpectedPublisherSha256 = '<OWNER_CERTIFICATE_64_HEX>'
if ((Get-FileHash -LiteralPath $Setup -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedSetupSha256) { throw 'Setup byte fingerprint mismatch' }
$signature = Get-AuthenticodeSignature -LiteralPath $Setup
if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) { throw 'Setup Authenticode trust unavailable' }
$sha = [Security.Cryptography.SHA256]::Create()
try { $observedPublisher = [BitConverter]::ToString($sha.ComputeHash($signature.SignerCertificate.RawData)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
if ($observedPublisher -cne $ExpectedPublisherSha256) { throw 'Unapproved Windows publisher' }
powershell.exe -NoProfile -ExecutionPolicy AllSigned -File $Setup -Bundle '<PROTECTED_STAGING>\bundle' -ManifestSHA256 '<OWNER_ENVELOPE_64_HEX>' -TrustedPublicKeySHA256 '<OWNER_ED25519_64_HEX>' -TrustedPublisherSHA256 $ExpectedPublisherSha256 -ExpectedSourceSHA '<FINAL_40_HEX_SHA>' -Mode production
```

Never use ExecutionPolicy Bypass, unsigned elevation, automatic certificate import
from the bundle, or a fixture certificate/key for this production gate. A stricter
domain policy must be respected. The installer and installed launch/removal
shortcuts must enforce AllSigned for production. Test-mode fixtures may remain
unsigned only when explicitly labeled as such; they cannot count as production
Authenticode or AllSigned acceptance.

### Rotation, revocation and pin freshness

The owner maintains an independently authenticated, administrator-protected trust
record containing an increasing policy sequence, issue/expiry times, approved
source SHAs, both active signer fingerprints, approved artifact digests and
explicitly revoked Ed25519/certificate fingerprints. Check that record before
installation or upgrade. Missing, expired, rolled-back or revoked owner trust
means refusal; neither a package-embedded key nor Windows' cached certificate
status may renew it. The present low-level verifier accepts an explicit pin and
has no automatic revocation feed: supplying a stale revoked pin would still
verify mathematically and is not an authorization decision.

For planned rotation, the owner publishes a newer authenticated trust record,
independently approves the new certificate/Ed25519 fingerprints, signs new script
artifacts and manifests, and updates protected operator pins before activation.
No trust-on-first-use, same-manifest self-rotation or automatic trust-store import
is allowed. For compromise, revoke the affected fingerprints in the owner record,
remove their installation/upgrade authorization, and update the Windows publisher
policy through the owner's normal administrative process. A timestamp does not
override an explicit owner revocation.

Existing installations keep their exact original source, envelope, ledger and
native-resource identities. Rotation must not overwrite that evidence or silently
substitute a new key into an old ledger. Cleanup of a retired installation needs
an explicitly owner-approved exact-digest cleanup allowance and the normal typed
confirmations; a compromised signer requires an incident recovery review using
newly trusted tooling. Do not re-enable a revoked key merely to make old code run.
No production key, certificate, rotation, trust-store change or production signing
has been performed as part of these candidate tests.

### Reproduce finalized signed archives using a frozen signature overlay

Authenticode/TSA is performed **once**, not independently for each comparison.
Freeze the three accepted signed script byte strings and the script-signing
receipt as reviewed build inputs. Keep the original unsigned source bundle, and
require the signed files to contain its exact byte prefix followed only by the
Authenticode comment block. Encoding conversion or executable source-body
changes fail this check and require a new reviewed source/build; they must not
be explained away as a signature difference.

From the trusted owner console, after the signing/chain/certificate-pin checks
above, create a fresh protected overlay directory and run this prefix check for
each of the three canonical relative paths. The paths and expected certificate
fingerprint come from the same approved signing receipt:

```powershell
$Unsigned = 'D:\Release\pristine-unsigned'
$Signed = 'D:\Release\owner-signed-once'
$Overlay = 'D:\OwnerApproved\frozen-script-overlay'
$OwnerCertificateSha256 = '<OWNER_APPROVED_64_HEX_DER_SHA256>'
$ScriptPaths = @('Setup-Tester.ps1','payload/scripts/windows/Start-ReleaseTester.ps1','payload/scripts/windows/Uninstall-ReleaseTester.ps1')
foreach ($relative in $ScriptPaths) {
    $before = [IO.File]::ReadAllBytes((Join-Path $Unsigned $relative))
    $after = [IO.File]::ReadAllBytes((Join-Path $Signed $relative))
    if ($after.Length -le $before.Length) { throw 'Missing signature-only suffix' }
    for ($i=0; $i -lt $before.Length; $i++) { if ($before[$i] -ne $after[$i]) { throw 'Signing changed the approved source body' } }
    $tail = [Text.Encoding]::ASCII.GetString($after,$before.Length,$after.Length-$before.Length)
    if ($tail -notmatch '\A[\r\n]*# SIG # Begin signature block\r?\n(?:#[^\r\n]*\r?\n)*# SIG # End signature block[\r\n]*\z') { throw 'Executable bytes outside approved source body' }
    $sig = Get-AuthenticodeSignature -LiteralPath (Join-Path $Signed $relative)
    if ($sig.Status -ne 'Valid' -or $null -eq $sig.TimeStamperCertificate) { throw 'Frozen signature is not accepted' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $pin=[BitConverter]::ToString($sha.ComputeHash($sig.SignerCertificate.RawData)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
    if ($pin -cne $OwnerCertificateSha256) { throw 'Frozen publisher differs from approved certificate' }
    $target = Join-Path $Overlay $relative
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
    [IO.File]::WriteAllBytes($target,$after)
}
Copy-Item -LiteralPath (Join-Path $Signed 'script-signing-receipt.json') -Destination (Join-Path $Overlay 'script-signing-receipt.json')
```

Use a fresh, already protected overlay path; the command does not establish trust
in an existing arbitrary directory. Record the overlay files' hashes in the
owner release evidence and keep that overlay immutable. Do not sign those
scripts again. Make **two clean builds** from the same exact Git commit and the
same independently approved component pins, then copy only the frozen three
script files and identical signing receipt into each build. Verify every unsigned
script's original hash against the receipt before replacing it, and every copied
script's final hash against the frozen receipt afterward.

```powershell
$SourceSha = '<FINAL_40_HEX_SHA>'
$Repo = 'D:\TrustedCheckout\tester'
$Components = 'D:\OwnerApproved\components'
$Pins = 'D:\OwnerApproved\component-pins.json'
$BuildA = 'D:\Release\clean-a'
$BuildB = 'D:\Release\clean-b'
python scripts/build_release.py --repo $Repo --source-sha $SourceSha --components $Components --component-pins $Pins --output $BuildA
python scripts/build_release.py --repo $Repo --source-sha $SourceSha --components $Components --component-pins $Pins --output $BuildB
$receipt = Get-Content -LiteralPath (Join-Path $Overlay 'script-signing-receipt.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($build in @($BuildA,$BuildB)) {
    foreach ($row in $receipt.scripts) {
        $target = Join-Path $build $row.path
        $frozen = Join-Path $Overlay $row.path
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -cne $row.source_sha256) { throw 'Clean build source differs from approved body' }
        if ((Get-FileHash -LiteralPath $frozen -Algorithm SHA256).Hash.ToLowerInvariant() -cne $row.signed_sha256) { throw 'Frozen signed bytes changed' }
        [IO.File]::WriteAllBytes($target,[IO.File]::ReadAllBytes($frozen))
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -cne $row.signed_sha256) { throw 'Signed overlay copy changed' }
    }
    [IO.File]::WriteAllBytes((Join-Path $build 'script-signing-receipt.json'),[IO.File]::ReadAllBytes((Join-Path $Overlay 'script-signing-receipt.json')))
}
python -m src.api.release_security manifest --bundle $BuildA --source-sha $SourceSha --output D:\Release\clean-a.post-authenticode.json
python -m src.api.release_security manifest --bundle $BuildB --source-sha $SourceSha --output D:\Release\clean-b.post-authenticode.json
python -m src.api.release_security sign --bundle $BuildA --private-key D:\OwnerKeys\tester-release.pk8.pem --source-sha $SourceSha --manifest-input D:\Release\clean-a.post-authenticode.json
python -m src.api.release_security sign --bundle $BuildB --private-key D:\OwnerKeys\tester-release.pk8.pem --source-sha $SourceSha --manifest-input D:\Release\clean-b.post-authenticode.json
python -c "from scripts.build_release import archive; print(archive(r'D:\Release\clean-a',r'D:\Release\clean-a.zip')); print(archive(r'D:\Release\clean-b',r'D:\Release\clean-b.zip'))"
if ((Get-FileHash D:\Release\clean-a.zip -Algorithm SHA256).Hash -cne (Get-FileHash D:\Release\clean-b.zip -Algorithm SHA256).Hash) { throw 'Final signed archives are not byte-identical' }
```

Both manifest operations must produce identical canonical inventories. Both
Ed25519 operations use the same approved external key over identical canonical
bytes, while the Authenticode bytes are reused verbatim. Compare complete ZIP
SHA256 values and record both builds' exact source/component/overlay inputs.
This tests reproducible assembly from **pinned signed artifacts**; it does not
claim independently timestamped Authenticode operations are reproducible.
The owner certificate/key are absent here, so production AllSigned/signature
and this production reproducibility gate remain **NOT RUN**. Fixture-signature
archive comparisons do not satisfy that gate.
