# Production keys: Ed25519 release key and Authenticode certificate

**For the owner only.** No production key exists in this repository, in CI, or in any agent environment, and none may ever be put there. Agents never receive, generate, hold or use production private keys or passphrases. Everything below runs on the owner's own signing workstation. The signing order itself, including the Authenticode-then-Ed25519 sequence and verification, is described in [security-signing.md](security-signing.md). This page covers creating, protecting, distributing and rotating the keys.

TESTER has **two independent trust roots**:

| Trust root | Protects | Pinned by |
|---|---|---|
| Ed25519 release key | The exact payload inventory (`release-manifest.json`). Verified by `src.api.release_security verify` and by Setup before running bundled Python. | SHA-256 of the raw 32-byte public key, delivered out of band. |
| Authenticode code-signing certificate | The PowerShell entry scripts (`Setup-Tester.ps1`, `Start-ReleaseTester.ps1`, `Uninstall-ReleaseTester.ps1`) under `AllSigned`. | SHA-256 of the certificate's raw DER, delivered out of band. The store thumbprint is only a locator. |

## 1. Signing workstation

- A dedicated Windows machine, or a dedicated user on a hardened machine, with full-disk encryption (BitLocker) and current patches.
- No development tools beyond what signing needs. No browser sessions, mail or chat on that account.
- It is offline while the Ed25519 key is generated, and ideally while signing too.
- A separately verified TESTER checkout at the exact release SHA: verify the commit and tree hash against GitHub from another machine. Use the bundled or a separately installed Python with `cryptography`.
- Never use the CI runner, an agent sandbox, or a shared or cloud development machine.

## 2. Create the Ed25519 release key (once)

Run this offline on the signing workstation. It writes only the **encrypted** PKCS#8 private key and prints the public fingerprint. The passphrase is typed interactively and never written to disk, shell history or a command line.

```powershell
python -I -B -c @"
import getpass, hashlib, pathlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
target = pathlib.Path(r'D:\OwnerKeys\tester-release.pk8.pem')
if target.exists():
    raise SystemExit('refusing to overwrite an existing key')
p1 = getpass.getpass('New key passphrase (>= 20 chars): ')
if len(p1) < 20 or p1 != getpass.getpass('Repeat: '):
    raise SystemExit('passphrase rejected')
key = Ed25519PrivateKey.generate()
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.BestAvailableEncryption(p1.encode()))
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(pem)
raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print('PUBLIC_KEY_HEX     ', raw.hex())
print('PUBLIC_KEY_SHA256  ', hashlib.sha256(raw).hexdigest())
"@
```

Then:

1. Restrict `D:\OwnerKeys` to the owner account only: `icacls D:\OwnerKeys /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F"`.
2. Make **two offline backups** of the encrypted file, for example on two encrypted USB drives kept in different places. Store the passphrase separately from both, for example in a password manager the signing workstation does not sync, or sealed on paper.
3. Record `PUBLIC_KEY_SHA256` in your own records. It is the value the installer pins as `trusted_public_key_sha256`.
4. Test once: sign a throwaway bundle with `release_security sign` and check it with `release_security verify --trusted-public-key-sha256 <value>`.

**Never:**

- commit the PEM, paste it anywhere, send it to an agent or CI;
- store it unencrypted;
- reuse it for anything else;
- let a package carry its own trusted key.

## 3. Authenticode code-signing certificate

Since 1 June 2023 the CA/Browser Forum code-signing baseline requires publicly trusted code-signing keys to be generated and kept in certified hardware: FIPS 140-2 Level 2 or Common Criteria EAL 4+, non-exportable. Choose one of these:

- **A hardware token or HSM** supplied or approved by a public CA (OV or EV code signing). The key is generated on the device and never leaves it. Sign with `Set-AuthenticodeSignature` (as in `security-signing.md`) or `signtool sign /fd SHA256 /tr <TSA> /td SHA256`, selecting the certificate on the token.
- **A cloud signing service backed by an HSM**, for example Microsoft Artifact Signing (formerly Trusted Signing) used with SignTool and its dlib.
  - These certificates are **short-lived** (days), so always add an RFC 3161 timestamp.
  - The certificate DER SHA-256 pinned by TESTER changes with each certificate. For every release, pin the DER hash of the certificate that actually signed it, delivered with that release's approval in #88.
  - Accept the CA's publisher name, not an individual certificate, only if the installer's pinning model is changed through a reviewed proposal. Today it pins the exact certificate.

For every signature:

- SHA-256 file digest;
- an RFC 3161 timestamp from the CA's timestamp authority;
- `Get-AuthenticodeSignature` reports `Valid` with a `TimeStamperCertificate` (the check in `security-signing.md` enforces this).

Do not create a self-signed "production" certificate, export the private key into the package, or import the owner's certificate into a CI runner.

## 4. Distributing the pins (independent channel)

For each release, the owner publishes in issue #88, from the owner account:

- the exact source SHA;
- the package archive SHA-256;
- the Ed25519 `PUBLIC_KEY_SHA256`;
- the Authenticode certificate DER SHA-256.

The person installing confirms these values through a second channel the owner controls, for example a message or call, before running Setup. Values that only appear inside the package are never trusted.

## 5. Rotation, compromise and revocation

- **Planned rotation:** generate a new Ed25519 key (§2) and publish its fingerprint with the next release. Installed systems are uninstalled and reinstalled from the new release, as `security-signing.md` describes. Old ledgers are never re-signed with a new key.
- **Suspected compromise of either key:**
  1. Stop publishing immediately.
  2. Revoke the certificate with the CA or cloud service.
  3. Record the compromised fingerprints as revoked in #88.
  4. Create new keys.
  5. Rebuild and re-sign from a verified checkout.
  6. Tell every installation to verify against the new pins only.
  A revoked key is never re-enabled to make old code run.
- **Lost Ed25519 key with no backup:** treat it like rotation. Existing installations keep working, but future packages need the new pin.

## 6. Checklist before each production signing

- [ ] The candidate SHA was independently accepted (Guardians plus owner) and CI is green on that exact SHA.
- [ ] The package was built by CI from that SHA. The archive SHA-256 is recorded, and the inventory and SBOM were reviewed outside the payload.
- [ ] The signing workstation has been checked (§1). Production keys come only from the owner's storage.
- [ ] Authenticode signing is done and timestamped. The post-Authenticode inventory is recomputed. Ed25519 signing is done. `release_security verify` passes with the out-of-band pins.
- [ ] The pins are published in #88 (§4). VM acceptance (`vm-acceptance.md`) has been run against the signed package.
