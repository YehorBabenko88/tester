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

The manifest contains exactly schema `tester-release/v1`, release_version `1`, source_sha, repository URL and relative-POSIX file hashes. Ed25519 signs `TESTER-INSTALL-MANIFEST-v1` followed by a NUL byte and sorted compact UTF-8 JSON. The package envelope contains manifest, signature_hex and public_key_hex. Verify with the owner-pinned key before packaging. `scripts.build_release.archive(bundle, zip_path)` emits byte-stable sorted ZIP entries with fixed metadata; run it after signing, then record the ZIP SHA-256 outside the bundle. Regenerate signatures after any payload change.

Required evidence includes exact SHA/diff/file inventory, CI URLs, SBOM/component pins, installer hash, native SCM and browser evidence, dedicated PostgreSQL and security evidence, known limitations and independent MASTER/four-Guardian decisions. Skips/UNKNOWN/NOT RUN remain open gates. Native SCM CI explicitly requires administrator privileges and cannot silently pass by skipping.

Do not merge main, publish a production release/tag, remove remote branches or install/reboot ADMINIT until independent GO and the owner's final authorization. A local unsigned package is reviewable engineering output, not a production release.

## Disposable native CI provisioning

`python -B scripts/release_ci_components.py --repo . --source-sha <exact-checked-out-HEAD> --work <fresh-scratch-directory> --evidence <sanitized-evidence-directory> --github-env <GITHUB_ENV-file>` automates the full Windows fixture bundle. It downloads the independently reviewed archive SHA-256 pins recorded in the supplier evidence, validates the pinned PSF and EDB executable signers, retrieves actual hash-locked Windows wheels, stages the complete private bootstrap, builds immutable source blobs and uses a freshly generated external **fixture-only** Ed25519 key. The private fixture key stays outside the bundle and uploaded evidence; this never creates production trust.

The script exports `TESTER_RELEASE_BUNDLE`, `TESTER_RELEASE_MANIFEST_SHA256`, `TESTER_RELEASE_PUBLIC_KEY_SHA256` and `TESTER_RELEASE_SOURCE_SHA` for `test_full_native_installer_lifecycle`. The full-installer CI job requires an elevated disposable Windows runner and explicitly sets `TESTER_REQUIRE_FULL_INSTALLER=1`; missing privileges, inputs or runtime must fail rather than silently skip. Pytest tooling runs outside the signed runtime. `PYTHONDONTWRITEBYTECODE=1` propagates to child processes to keep signed component trees immutable.

The hosted fixture checks temporary UUID-owned PostgreSQL/application services, recovery/data preservation, shortcut helper behavior and independent transient uninstall. It does not establish Windows 11 physical reboot acceptance, visual browser acceptance, independent Guardian GO or owner production signing. Those remain separate evidence gates.
