# M2 SourceOS Recovery/Installer Packaging Spec

This document defines the packaging responsibilities for two SourceOS boot entries on
Apple Silicon (M2) hardware: the **normal boot entry** and the **Recovery/Installer entry**.
It also notes where the same model applies to non-Apple-Silicon targets.

> **Scope**: Documentation and dry-run fixtures only. No host mutation, disk writes,
> boot-entry creation, or installer execution is performed. Real Apple Silicon boot-entry
> mutation, installer disk writes, rollback execution, and kexec are explicitly out of scope.

## Background

Apple Silicon uses an Asahi-style boot picker and install flow. Each OS entry in the boot
picker corresponds to a container OS stub that launches a platform-specific boot loader
chain (m1n1 → U-Boot → kernel). SourceOS must package two distinct entries:

| Entry type | Boot picker label | Primary channel | Primary action |
|---|---|---|---|
| Normal boot | `SourceOS` | `live` | `enroll` / `fetch` / `verify` |
| Recovery/Installer | `SourceOS Recovery` | `recovery`, `installer` | `install` / `repair` / `rollback` |

This two-entry model mirrors the Asahi Linux approach and the SourceOS/NLBoot evidence
model: each entry has its own `BootReleaseSet`, its own signed manifest, and its own
evidence envelope.

## Required inputs

Both entry types consume the following inputs before producing a `BootReleaseSet`:

| Input | Type | Source | Notes |
|---|---|---|---|
| `BootReleaseSet` | `sourceos.dev/v1 BootReleaseSet` | Prophet Platform / lattice-forge | Handoff object; canonical schema in `SourceOS-Linux/sourceos-spec` |
| `ReleaseSet` | reference string | Prophet Platform | Points to the upstream release set via `spec.releaseSetRef` |
| `NLBoot plan` | `nlboot BootPlan` (side-effect-free, `execute=false`) | SociOS-Linux/nlboot | Provides signed manifest verification and channel mapping; consumed by `SourceOSBootAdapter` |
| Artifact cache evidence | SHA-256 digests + URIs | lattice-forge build | Feeds `spec.artifacts[*].sha256` fields; content-addressed; verified before use |
| `AppleSiliconAdapterEvidence` | platform evidence record | local PAL (design stub) | Device claim, Secure Enclave fingerprint, boot nonce; normalises into `BootEvidence` |
| `BootProofRecord` | evidence envelope | SourceOS Boot adapter | Emitted after plan stage; covers device-claim, manifest-hash, verification-result, channel, boot-mode |

> **Note**: `BootReleaseSet` and `ReleaseSet` canonical schemas live in
> `SourceOS-Linux/sourceos-spec`. This repo does not duplicate those schemas; it
> references them by `apiVersion`/`kind` and validates examples with the validator in
> `src/sourceos_boot/validate_boot_release_set.py`.

## Normal boot entry packaging

### Responsibilities

1. **Declare** a `BootReleaseSet` with `channels: ["live"]` and `platforms: ["apple-silicon"]`.
2. **Reference** the NLBoot-verified kernel, initrd, rootfs, and m1n1/U-Boot bootloader artifacts.
3. **Set policy** `diskWrite: "forbidden"` — normal boot must not write to disk.
4. **Require** enrollment-only network access during boot (`network: "enrollment-only"`).
5. **Collect** evidence reports: `device-claim`, `manifest-hash`, `verification-result`,
   `selected-channel`, `boot-mode`, `measurement`, `attestation`.
6. **Sign** the manifest with Sigstore/cosign and record the bundle reference.
7. **Publish** TUF metadata reference for the artifact set.

### Packaging inputs → BootReleaseSet fields

| Input | BootReleaseSet field |
|---|---|
| `ReleaseSet` ref | `spec.releaseSetRef` |
| NLBoot channel mapping (`bootstrap` → `live`) | `spec.channels` |
| Artifact cache URIs + SHA-256 digests | `spec.artifacts[*].uri`, `spec.artifacts[*].sha256` |
| `AppleSiliconAdapterEvidence.deviceClaim` | `spec.evidence.correlationId`, proof gate |
| `BootProofRecord` | `spec.evidence.requiredReports` |
| Build provenance (SLSA, in-toto) | `spec.provenance` |
| Sigstore bundle | `spec.signature` |
| TUF metadata | `spec.trust` |

### Dry-run fixture

`examples/m2-recovery-installer/normal-boot.example.json` — a minimal valid
`BootReleaseSet` for the M2 normal boot entry. This fixture is syntax-checked by
`make validate`.

## Recovery/Installer entry packaging

### Responsibilities

1. **Declare** a `BootReleaseSet` with `channels: ["recovery", "installer"]` and
   `platforms: ["apple-silicon"]`.
2. **Reference** the recovery kernel, initrd, recovery image, and Asahi-style installer
   data artifact.
3. **Set policy** `diskWrite: "installer-only"` — restricted to the install/repair path;
   enabled only under a valid enrollment token.
4. **Allow** install, repair, rollback, rekey, and attestation actions under token.
5. **Require** enrollment-only network (`network: "enrollment-only"`).
6. **Collect** evidence reports: `device-claim`, `manifest-hash`, `verification-result`,
   `selected-channel`, `boot-mode`, `install-result`, `rollback-result`,
   `measurement`, `attestation`.
7. **Sign** the manifest and record the Sigstore bundle reference.
8. **Publish** TUF metadata reference and anti-rollback minimum version.

### Packaging inputs → BootReleaseSet fields

| Input | BootReleaseSet field |
|---|---|
| `ReleaseSet` ref | `spec.releaseSetRef` |
| NLBoot channel mapping (`installer` → `installer`, `recovery` → `recovery`) | `spec.channels` |
| Recovery image + installer data URIs + SHA-256 | `spec.artifacts[*]` |
| `AppleSiliconAdapterEvidence.deviceClaim` | `spec.evidence.correlationId`, proof gate |
| `BootProofRecord` | `spec.evidence.requiredReports` |
| Build provenance (SLSA, in-toto) | `spec.provenance` |
| Sigstore bundle | `spec.signature` |
| TUF metadata | `spec.trust` |
| Anti-rollback minimum version | `spec.antiRollback.minimumVersion` |

### Dry-run fixture

`examples/m2-recovery-installer/recovery-installer.example.json` — a minimal valid
`BootReleaseSet` for the M2 Recovery/Installer entry. This fixture is syntax-checked by
`make validate`.

## Asahi-style boot picker integration

The Apple Silicon boot picker (Startup Security Utility / `kmutil`) selects a container
OS by reading a stub that points to the first-stage bootloader. SourceOS normal boot and
Recovery/Installer entries each occupy a distinct container OS slot.

The packaging adapter (design stub; not yet implemented) maps a `BootReleaseSet` into a
platform entrypoint descriptor:

```
BootReleaseSet
  └─ spec.channels: ["live"]         → normal-boot container OS stub
  └─ spec.channels: ["recovery",     → recovery/installer container OS stub
                     "installer"]
```

The adapter must remain side-effect-free (dry-run, `execute=false`) until a signed
platform policy record explicitly enables boot-entry mutation.

## Non-Apple-Silicon targets

The same `BootReleaseSet` model applies to non-Apple-Silicon platforms. The
`spec.platforms` array is extensible:

| Platform | Entrypoint kind |
|---|---|
| `apple-silicon` | Asahi-style container OS stub |
| `uefi-x86_64` | UEFI boot entry / iPXE menu |
| `uefi-aarch64` | UEFI boot entry |
| `generic-arm64` | U-Boot / iPXE menu |

M2 is the first-class proof hardware for this milestone. Other platform PAL notes will be
added in subsequent slices (see `WORLD_CLASS_TARGETS.md`).

## Evidence model

Both entry types emit a `BootProofRecord` (via `SourceOSBootAdapter`) that covers:

- `device-claim` — device identity and public-key fingerprint
- `manifest-hash` — SHA-256 of the signed nlboot manifest document
- `verification-result` — `pass` / `fail` from RSA-PSS/SHA-256 manifest verification
- `selected-channel` — the channel selected by the nlboot boot-mode mapping
- `boot-mode` — the nlboot `boot_mode` value
- `install-result` / `rollback-result` — emitted only by the recovery/installer entry
- `measurement` — platform measurement claim (Apple Silicon Secure Enclave or TPM)
- `attestation` — SLSA / in-toto attestation reference

Evidence is assembled by `SourceOSBootAdapter.build_evidence_from_nlboot_manifest` and
recorded in `spec.evidence.requiredReports`.

## Adapter flow

```
NLBoot plan (execute=false)
  │
  ├─ boot_release_set_patch_from_nlboot_manifest()
  │     → spec.artifacts, spec.channels, spec.releaseSetRef
  │
  ├─ authorization_from_nlboot_token()
  │     → BootAuthorization (tokenId, expiresAt, correlationId)
  │
  └─ build_evidence_from_nlboot_manifest()
        → BootProofRecord (device-claim, manifest-hash, …)

AppleSiliconAdapterEvidence (design stub)
  │
  └─ normalise_apple_silicon_evidence()        ← not yet implemented
        → spec.evidence.requiredReports += ["measurement"]
```

All stages are pure and side-effect-free. Host mutation is disabled.

## Known gaps

- `AppleSiliconAdapterEvidence` normalization is a design stub; no Apple Silicon
  Secure Enclave or SEP interaction is implemented.
- Real nlboot RSA-PSS/SHA-256 manifest signature verification is not yet wired into
  the adapter.
- Actual boot-entry creation, disk writes, and installer execution are not implemented
  and are explicitly out of scope for this milestone.
- Artifact content-addressed cache fetch and SHA-256 verification are not yet
  implemented; digests in fixtures use placeholder zeros.

## References

- [`NLBOOT_INTEGRATION.md`](NLBOOT_INTEGRATION.md) — nlboot adapter integration guide
- [`NLBOOT_COMPATIBILITY.md`](NLBOOT_COMPATIBILITY.md) — nlboot field-level mapping
- [`INTEGRATION.md`](INTEGRATION.md) — upstream/downstream dependency contract
- [`WORLD_CLASS_TARGETS.md`](WORLD_CLASS_TARGETS.md) — implementation roadmap
- [`examples/m2-recovery-installer/normal-boot.example.json`](../examples/m2-recovery-installer/normal-boot.example.json) — normal boot fixture
- [`examples/m2-recovery-installer/recovery-installer.example.json`](../examples/m2-recovery-installer/recovery-installer.example.json) — recovery/installer fixture
- [`src/sourceos_boot/adapter.py`](../src/sourceos_boot/adapter.py) — SourceOSBootAdapter implementation
- [`SourceOS-Linux/sourceos-spec`](https://github.com/SourceOS-Linux/sourceos-spec) — canonical BootReleaseSet and ReleaseSet schemas
- [`SociOS-Linux/nlboot`](https://github.com/SociOS-Linux/nlboot) — upstream safe planner
