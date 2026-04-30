# AppleSiliconAdapterEvidence Normalization Design

This document defines the normalization design for `AppleSiliconAdapterEvidence` into
SourceOS `BootEvidence` / `BootProofRecord`. It covers the field-level mapping, dry-run
fixture walkthrough, and boundaries.

> **Scope**: Documentation and dry-run fixtures only. No Secure Enclave calls, SEP
> interaction, host mutation, disk writes, boot-entry creation, or installer execution is
> performed or described here. This is a pure data-mapping design.

## Background

Apple Silicon devices expose a Secure Enclave Processor (SEP) that can provide:

- A device identity claim bound to the Silicon hardware key
- A boot nonce unique to each boot transaction
- A measurement digest covering the boot chain (iBoot, m1n1, U-Boot)
- A chip/board identifier pair for hardware attestation

SourceOS collects this evidence through a platform abstraction layer (PAL) that produces
an `AppleSiliconAdapterEvidence` record. This record must be normalized into a
`BootEvidence` envelope (see `SourceOS-Linux/sourceos-spec: BootProofRecord`) before it
can be recorded in `spec.evidence.requiredReports` of a `BootReleaseSet`.

> **Note**: `AppleSiliconAdapterEvidence`, `BootProofRecord`, `ReleaseSet`, `Fingerprint`,
> `ConfigSource`, `TokenDoor`, and `GitRefBuild` canonical schemas live in
> `SourceOS-Linux/sourceos-spec`. This repo does not duplicate those schemas; it references
> them by `apiVersion`/`kind` and produces fixtures compatible with that contract.

## Input: AppleSiliconAdapterEvidence

| Field | Type | Description |
|---|---|---|
| `deviceClaim.deviceId` | string | Stable device identifier |
| `deviceClaim.publicKeyFingerprint` | `sha256:<hex>` | Device public key fingerprint |
| `deviceClaim.platform` | `"apple-silicon"` | Platform discriminator |
| `deviceClaim.nonce` | string | Per-boot nonce emitted by the boot environment |
| `deviceClaim.observedAt` | ISO 8601 timestamp | Timestamp at which the claim was observed |
| `secureEnclaveFingerprint` | `sep:sha256:<hex>` | SEP-attested hardware fingerprint (dry-run: placeholder) |
| `bootNonce` | string | SEP-bound boot nonce (dry-run: placeholder) |
| `measurementDigest` | `sha256:<hex>` | Boot-chain measurement digest (dry-run: placeholder zeros) |
| `chipId` | string | Apple Silicon chip identifier (e.g. `"Apple M2"`) |
| `boardId` | string | Board identifier (e.g. `"0x00"`) |
| `bootMode` | string | nlboot boot mode (`"bootstrap"`, `"recovery"`, `"installer"`, `"ephemeral"`) |
| `correlationId` | string | Boot transaction correlation identifier |

Dry-run fixture: [`examples/apple-silicon-evidence/raw-apple-silicon-evidence.example.json`](../examples/apple-silicon-evidence/raw-apple-silicon-evidence.example.json)

## Normalization: AppleSiliconAdapterEvidence → BootEvidence

The normalization function `normalise_apple_silicon_evidence()` (design stub; not yet
implemented) performs a pure, side-effect-free field mapping. It does not call the SEP,
write to disk, mutate boot entries, or perform any host-changing operation.

### Field mapping

| Input field | Output field | Notes |
|---|---|---|
| `correlationId` | `bootEvidence.correlationId` | Passed through unchanged |
| `deviceClaim.deviceId` | `bootEvidence.deviceId` | Passed through unchanged |
| `bootMode` → channel map | `bootEvidence.selectedChannel` | Same `BOOT_MODE_TO_CHANNEL` map as the nlboot adapter |
| `bootMode` | `bootEvidence.bootMode` | Passed through unchanged |
| _(manifest hash; provided by caller)_ | `bootEvidence.manifestHash` | Not present in raw evidence; caller supplies post-verify |
| _(verification result; provided by caller)_ | `bootEvidence.verificationResult` | `"pass"` / `"fail"` from RSA-PSS/SHA-256 manifest verification |
| `deviceClaim.*` | `normalizedReports["device-claim"]` | Full device claim object |
| `secureEnclaveFingerprint`, `bootNonce`, `measurementDigest`, `chipId`, `boardId` | `normalizedReports["measurement"]` | Grouped under the `measurement` report |

### Output: BootEvidence / BootProofRecord patch

```
bootEvidence.reports = [
  "device-claim",
  "manifest-hash",
  "verification-result",
  "selected-channel",
  "boot-mode",
  "measurement"          ← added by Apple Silicon normalization
]
```

The `measurement` report requires the `secureEnclaveFingerprint` and `measurementDigest`
fields. In dry-run mode, these are placeholder zero digests. In production, they would be
populated from a real SEP attestation call (which is explicitly out of scope here).

Dry-run fixture: [`examples/apple-silicon-evidence/normalized-boot-evidence.example.json`](../examples/apple-silicon-evidence/normalized-boot-evidence.example.json)

## Normalization flow

```
AppleSiliconAdapterEvidence (raw)
  │
  └─ normalise_apple_silicon_evidence()          ← pure; no side effects
        │
        ├─ map bootMode → selectedChannel
        ├─ pass through deviceClaim → reports["device-claim"]
        ├─ group SEP fields → reports["measurement"]
        └─ append "measurement" to requiredReports
              → BootEvidence
              → BootProofRecord patch
                  spec.evidence.requiredReports += ["measurement"]
```

All stages are pure and side-effect-free. The normalization function is designed to be
called after the nlboot manifest verification stage and before the evidence is recorded
in the `BootReleaseSet`.

## Integration with the existing adapter flow

```
NLBoot plan (execute=false)
  │
  ├─ boot_release_set_patch_from_nlboot_manifest()
  │     → spec.artifacts, spec.channels, spec.releaseSetRef
  │
  ├─ authorization_from_nlboot_token()
  │     → BootAuthorization (tokenId, expiresAt, correlationId)
  │
  ├─ build_evidence_from_nlboot_manifest()
  │     → BootEvidence (device-claim, manifest-hash, …)
  │
  └─ normalise_apple_silicon_evidence()           ← adds measurement report
        → BootEvidence with reports += ["measurement"]
        → BootProofRecord patch

All stages: pure, side-effect-free, execute=false.
```

## Non-Apple-Silicon targets

The same `BootEvidence` structure applies to all platforms. The `measurement` report
source field distinguishes the platform:

| Platform | `measurement.source` | Measurement mechanism |
|---|---|---|
| `apple-silicon` | `"apple-silicon-sep"` | SEP attestation (dry-run: placeholder) |
| `uefi-x86_64` | `"tpm2-pcr"` | TPM2 PCR quote (design stub) |
| `uefi-aarch64` | `"tpm2-pcr"` | TPM2 PCR quote (design stub) |
| `generic-arm64` | `"tpm2-pcr"` | TPM2 PCR quote (design stub) |

M2 (Apple Silicon) is the first-class proof hardware for this milestone. Other platform
PAL notes are tracked in [`WORLD_CLASS_TARGETS.md`](WORLD_CLASS_TARGETS.md).

## Non-goals / boundaries

- No Secure Enclave (SEP) calls, kexec, boot-entry writes, disk writes, installer
  execution, or rollback execution.
- No duplication of canonical `AppleSiliconAdapterEvidence` or `BootProofRecord` schemas
  from `SourceOS-Linux/sourceos-spec`; this doc references them.
- No real hardware measurement values; all digests in fixtures are placeholder zeros.
- The normalization function is a design stub only; implementation requires explicit
  platform policy sign-off and SEP integration review.

## Known gaps

- `normalise_apple_silicon_evidence()` is not yet implemented in `src/sourceos_boot/adapter.py`.
- Real SEP fingerprint and measurement digest population require Secure Enclave
  integration (explicitly out of scope for this milestone).
- TPM2-based measurement for non-Apple-Silicon targets is a design stub only.

## References

- [`M2_RECOVERY_INSTALLER_PACKAGING.md`](M2_RECOVERY_INSTALLER_PACKAGING.md) — M2 packaging spec and adapter flow
- [`NLBOOT_INTEGRATION.md`](NLBOOT_INTEGRATION.md) — nlboot adapter integration guide
- [`examples/apple-silicon-evidence/raw-apple-silicon-evidence.example.json`](../examples/apple-silicon-evidence/raw-apple-silicon-evidence.example.json) — raw evidence fixture
- [`examples/apple-silicon-evidence/normalized-boot-evidence.example.json`](../examples/apple-silicon-evidence/normalized-boot-evidence.example.json) — normalized evidence fixture
- [`src/sourceos_boot/adapter.py`](../src/sourceos_boot/adapter.py) — SourceOSBootAdapter implementation
- [`SourceOS-Linux/sourceos-spec`](https://github.com/SourceOS-Linux/sourceos-spec) — canonical `AppleSiliconAdapterEvidence`, `BootProofRecord`, and related schemas
