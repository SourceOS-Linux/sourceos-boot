# World-Class Target Architecture: SourceOS Boot

This repo is not allowed to stop at a basic boot manifest validator. `sourceos-boot` must become a world-class boot, recovery, installation, and rollback control surface for SourceOS and Prophet Lattice.

## State-of-the-art bar

SourceOS Boot must align with these target classes:

1. **Supply-chain provenance**
   - SLSA provenance for boot artifacts, manifests, recovery images, and builders.
   - in-toto attestations for build, sign, promote, and publish steps.
   - recursive dependency capture for boot components and release inputs.

2. **Artifact signing and transparency**
   - Sigstore/cosign-compatible signing for boot manifests, recovery images, OCI artifacts, SBOMs, and release metadata.
   - Support for keyless signing during CI and KMS/HSM-backed signing for production roots.
   - Transparency-log verification where available.

3. **Secure update trust**
   - TUF-style repository metadata for boot/recovery artifact distribution.
   - Uptane-style threat model for device fleets, rollback prevention, freeze-attack resistance, and delegated trust.
   - Versioned, revocable trust roots.

4. **Measured boot and attestation**
   - Device claim must evolve from a simple public key to an attested boot identity.
   - Support measured boot evidence where platform allows it.
   - TPM/TEE/Secure Enclave/Apple Silicon evidence should be represented through a platform adaptation layer.
   - Every boot path must report measurement, manifest hash, selected channel, verification result, and recovery action.

5. **Platform adaptation without platform lock-in**
   - Apple Silicon PAL for Asahi-style install/recovery entries.
   - UEFI/iPXE PAL for PC/Purism-style hardware.
   - Small-bootstrap-media PAL for Wi-Fi-first or PXE-hostile environments.
   - PAL outputs must normalize into the same BootReleaseSet/EvidenceBundle model.

6. **Policy-first boot actions**
   - Boot modes: live, installer, recovery, rollback, rescue.
   - Disk writes must be forbidden by default and opened only by signed policy.
   - Network access must be scoped by boot mode and token lifecycle.
   - Recovery actions must be replayable and auditable.

7. **Observability**
   - OpenTelemetry-compatible traces, metrics, and logs for boot-controller services and control-plane interactions.
   - Boot evidence should correlate with Prophet Platform release, device, policy, and runtime IDs.

8. **Resilience**
   - Last-known-good BootReleaseSet fallback.
   - Offline recovery path.
   - Anti-rollback policy.
   - Partial-download and artifact verification safeguards.

## Non-negotiable product invariant

A SourceOS boot or recovery action is not valid unless it can answer:

- what artifact booted;
- who built it;
- where the source came from;
- which policy authorized it;
- which device claimed it;
- what measurements were observed;
- which action was taken;
- what changed on disk;
- how to roll back;
- and which evidence proves all of the above.

## Contract upgrades required

BootReleaseSet v1 must add:

- `provenance`: SLSA/in-toto references, builder identity, source commits, resolved dependencies.
- `trust`: TUF/Uptane repository metadata references, trust root, threshold signatures, expiry.
- `signature`: Sigstore/cosign bundle or production signing reference.
- `measurements`: platform-specific measurement claims normalized into evidence fields.
- `antiRollback`: minimum accepted version, rollback exceptions, offline policy.
- `telemetry`: OpenTelemetry trace context and evidence correlation IDs.

## Implementation path

1. Keep v0 validator simple and stable.
2. Add SLSA/in-toto provenance schema fragments.
3. Add Sigstore/cosign bundle references.
4. Add TUF metadata references.
5. Add platform measurement evidence model.
6. Add nlboot adapter around signed BootReleaseSet fetch/verify.
7. Add Apple Silicon PAL design notes.
8. Add UEFI/iPXE PAL design notes.
9. Add boot evidence emitter.

## Doctrine

Lattice is the control plane. SourceOS is the substrate. Fog is where execution happens.

`sourceos-boot` must make the substrate recoverable, inspectable, updateable, and provable.
