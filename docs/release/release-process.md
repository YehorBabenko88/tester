# Reproducible release process

The builder uses immutable Git objects from `--source-sha`; local modifications and untracked files are excluded. Python 3.14.10 Windows x64 production dependencies are pinned by the actual wheel bytes in `requirements-release-windows314.lock`, resolved with the existing platform constraints. The older `requirements-windows-lab.lock` is CPython 3.14 evidence and is not the release lock.

Prepare an offline component directory containing `runtimes/python/`, `runtimes/postgres/` and `wheelhouse/`. CPython must be complete (stdlib, venv/ensurepip and DLLs), with cryptography and its CFFI dependencies available before the installer verifies its Ed25519 signature. PostgreSQL requires bin/lib/share, never the installed data directory. A local read-only staging helper is available as `scripts.build_release.stage_local_components(python_root, postgres_root, wheelhouse, lock, output, pins_path)`. It installs only hash-locked offline wheels into the staged runtime, probes that isolated bootstrap and hashes every regular component file. Its provenance is local content measurement, **not vendor authentication**. Independently review and retain the resulting component pins before distribution.

To retrieve wheels for an empty wheelhouse on the supported target:

```powershell
python -m pip download --only-binary=:all: --require-hashes --platform win_amd64 --python-version 314 --implementation cp --abi cp314 --dest wheelhouse -r requirements-release-windows314.lock
```

Build from the final integrated, published revision:

```powershell
python scripts/build_release.py --repo . --source-sha '<exact-sha>' --components '<reviewed-components>' --component-pins '<reviewed-pins.json>' --output '<fresh-release-directory>'
```

The resulting status is `BLOCKED_UNSIGNED`. No test signing key establishes production trust. The owner supplies an encrypted external Ed25519 PKCS8 private key, keeps it outside the repository and bundle, and signs the canonical unsigned manifest using `python -m src.api.release_security sign --bundle DIR --private-key EXTERNAL_ENCRYPTED_PEM --source-sha SHA --manifest-input OUTSIDE_UNSIGNED_JSON`. The signer prompts for the passphrase. Follow the security owner's signing instructions and independently pin/distribute the public-key SHA-256, exact envelope SHA-256 and source SHA. Private keys and passphrases never belong in Git, CI artifacts or command logs.

The manifest contains exactly schema `tester-release/v1`, release_version `1`, source_sha, repository URL and relative-POSIX file hashes. Ed25519 signs `TESTER-INSTALL-MANIFEST-v1` followed by a NUL byte and sorted compact UTF-8 JSON. The package envelope contains manifest, signature_hex and public_key_hex. Verify with the owner-pinned key before packaging. `scripts.build_release.archive(bundle, zip_path, expected_inventory=approved)` emits byte-stable sorted ZIP entries with fixed metadata. For a signed bundle, obtain `approved` only from the externally pinned `verify_release` result: `dict(verified.files)` plus the exact independently approved `release-manifest.json` digest. Signed calls without that complete map fail closed; the archiver does not establish Ed25519 trust. Each captured byte string must match that map, and the complete input tree is rechecked before atomic no-overwrite publication. Unsigned default mode is only a nontrust snapshot. Record the final ZIP SHA-256 outside the bundle. Regenerate signatures after any payload change.

Required evidence includes exact SHA/diff/file inventory, CI URLs, SBOM/component pins, installer hash, native SCM and browser evidence, dedicated PostgreSQL and security evidence, known limitations and independent MASTER/four-Guardian decisions. Skips/UNKNOWN/NOT RUN remain open gates. Native SCM CI explicitly requires administrator privileges and cannot silently pass by skipping.

Do not merge main, publish a production release/tag, remove remote branches or install/reboot ADMINIT until independent GO and the owner's final authorization. A local unsigned package is reviewable engineering output, not a production release.

## Disposable native CI provisioning

`python -B scripts/release_ci_components.py --repo . --source-sha <exact-checked-out-HEAD> --work <fresh-scratch-directory> --evidence <sanitized-evidence-directory> --github-env <GITHUB_ENV-file>` automates the full Windows fixture bundle. It downloads the independently reviewed archive SHA-256 pins recorded in the supplier evidence, validates the pinned PSF and EDB executable signers, retrieves actual hash-locked Windows wheels, stages the complete private bootstrap, builds immutable source blobs and uses a freshly generated external **fixture-only** Ed25519 key. The private fixture key stays outside the bundle and uploaded evidence; this never creates production trust.

The script exports `TESTER_RELEASE_BUNDLE`, `TESTER_RELEASE_MANIFEST_SHA256`, `TESTER_RELEASE_PUBLIC_KEY_SHA256` and `TESTER_RELEASE_SOURCE_SHA` for `test_full_native_installer_lifecycle`. The full-installer CI job requires an elevated disposable Windows runner and explicitly sets `TESTER_REQUIRE_FULL_INSTALLER=1`; missing privileges, inputs or runtime must fail rather than silently skip. Pytest tooling runs outside the signed runtime. `PYTHONDONTWRITEBYTECODE=1` propagates to child processes to keep signed component trees immutable.

The hosted fixture checks temporary UUID-owned PostgreSQL/application services, recovery/data preservation, shortcut helper behavior and independent transient uninstall. It does not establish Windows 11 physical reboot acceptance, visual browser acceptance, independent Guardian GO or owner production signing. Those remain separate evidence gates.


## Frozen Authenticode script overlays

The optional `--signed-script-overlay <protected-owner-overlay> --trusted-publisher-sha256 <independent-raw-DER-certificate-SHA256>` build inputs consume the signing receipt and exact three frozen scripts defined in [security signing](security-signing.md). The builder requires receipt schema tester-script-signatures/v1, the exact source SHA and complete source/signed/certificate hashes. Every signed script must retain the original unsigned byte prefix and append only an ASCII Authenticode comment block. Extra files, body/encoding changes, duplicate receipt keys, wrong publisher pins and invalid or untimestamped Windows signatures fail before a build output is created.

The overlay is an already protected owner input, never a trust root obtained from the bundle. Its root, nested directories, scripts and receipt require protected SYSTEM/Administrators ownership and exact administrative ACLs; protecting only the top directory while leaving inherited child ACLs unreviewed is insufficient. The builder reuses the security module native no-write/delete leases, acquired top-down for all ancestors and overlay objects, reads captured bytes through the held handles, and verifies Authenticode while those leases remain held. The builder verifies signatures and assembles bytes; it does not sign scripts, import certificates, alter trust stores or acquire owner keys. Freeze Authenticode/TSA inputs once, build two clean copies from the same source/components/overlay, regenerate and Ed25519-sign the resulting full manifests using the owner procedure, and compare the final ZIP hashes. Repeated independent timestamp operations are not claimed to reproduce identical bytes. Unit fixture verification and fixture-key ZIP comparisons do not satisfy production Authenticode, AllSigned or owner signing gates.

## Existing lab installation upgrades (`C:\TesterLab`)

`scripts/windows/Update-TesterLabRelease.ps1` is an **app-only** in-place updater for the separately approved legacy lab installation. It is not part of the signed release route and never changes the PostgreSQL schema or the Python dependency set. During `NATIVE_AND_SERVICE_PREFLIGHT`, before the owned app service is stopped, it refuses the upgrade (fail-closed, nothing is changed) when:

- the SHA-256 of `requirements-windows-lab.lock` in the new payload differs from the installed payload (`Dependency/schema changes require a separate migration proposal.`), or
- any file under `src/api/migrations/` or `src/storage/migrations/` differs, or the number of migration files differs (`New migrations are outside app-only upgrade.`).

These checks are deliberate and must not be relaxed to make an upgrade pass.

**Dependency lock.** `requirements-windows-lab.lock` is byte-pinned to the content installed from `d6059af` (Git blob `f9813f1acfcbbc949275d4ee92388980e838e6c2`, SHA-256 `a0b59bdec87a6b96ff7a2bf35d99d3576fed5bcb4f48503593f0c28a3c82f70e`). Do not edit it, not even its comment lines: any byte change blocks every lab upgrade. Its first line refers to `docs/adminit-acceptance/DEPENDENCIES.md`, which was removed from the tree in PR #102 and remains readable with `git show d6059af:docs/adminit-acceptance/DEPENDENCIES.md`. `tests/platform/test_lab_upgrade_contract.py` enforces the pin (issue #110).

**Storage migration 008.** `src/storage/migrations/008_public_unknown_taker_side.sql` (added after `d6059af`) relaxes the trade-side CHECK constraints to admit `Unknown`. A lab on `d6059af` therefore cannot be moved to a revision that contains 008 with the app-only updater; it stops at preflight with the migration message above. There is intentionally no automated schema path in the lab updater. Until the owner approves a separate migration proposal, the lab stays on its current revision (BLOCKED, no data loss). Such a proposal must at least:

1. Run as an explicit owner/Administrator action, never self-elevating, with the owned app service stopped and the dedicated lab PostgreSQL cluster identity verified (same checks as the updater preflight). It must never contact system PostgreSQL on port 5432.
2. Take and verify a backup first using the installed old release (`scripts/lab_portable.py backup-restore`, which dumps `tester_lab` and verifies a restore into a scratch database), and retain the dump SHA-256.
3. Apply only the reviewed forward migrations with the owner (migration) role through `src.storage.migrations.migrate` from the new published source; the App role `tester_lab_app` has no DDL or `schema_migrations` write rights and must stay that way. 008 drops/re-adds CHECK constraints on `tester_storage.raw_records` (exclusive lock and full validation scan) and records its checksum in `tester_storage.schema_migrations`.
4. Define rollback as restoring the verified dump into the dedicated cluster; the migration runner is forward-only and refuses older clients when a newer version is recorded.
5. Only after the schema step succeeds, run the app-only updater for the code change. Its migration guard compares the old and new payload file lists, so the proposal must state how the approved schema state is recorded before the updater is run; the updater itself must not be weakened to skip that guard.

None of this has been executed against `C:\TesterLab`; it is UNVERIFIED on a real lab host.
