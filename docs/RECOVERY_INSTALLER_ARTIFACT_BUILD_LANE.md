# Recovery/Installer Artifact Build Lane

This document defines the **artifact build lane** for SourceOS Recovery/Installer
artifacts. It covers the required artifact classes, build pipeline stages, SBOM and
provenance references, and how the lane output feeds into the `BootReleaseSet` packaging
flow.

> **Scope**: Documentation and dry-run fixtures only. No host mutation, disk writes,
> boot-entry creation, installer execution, artifact publishing, or tagged-release
> creation is performed. Real Apple Silicon boot-entry mutation, installer disk writes,
> rollback execution, and kexec are explicitly out of scope.

## Background

A "build lane" is the CI/CD pipeline segment responsible for producing a verified,
content-addressed set of artifacts for one target surface. The Recovery/Installer lane
produces everything needed to fill the `spec.artifacts` array of a
`BootReleaseSet` with `channels: ["recovery", "installer"]`.

The lane is distinct from the *packaging* step (documented in
[`M2_RECOVERY_INSTALLER_PACKAGING.md`](M2_RECOVERY_INSTALLER_PACKAGING.md)) that
assembles a `BootReleaseSet` from lane outputs. The build lane runs first; packaging
runs after lane artifacts are verified.

## NLBoot release-candidate status

The lane references the NLBoot safe planner (SociOS-Linux/nlboot) at
**release-candidate** status. No tagged release has been published. The SBOM proof for
NLBoot RC artifacts is in progress; this lane tracks that status via the
`nlbootRef.status` and `sbomStatus` fields in the build-result fixture. Do not invent
release URLs or checksums.

## Required artifact classes

The Recovery/Installer lane must produce exactly the following artifact classes. Each
class is content-addressed (SHA-256), carries an SBOM reference, and carries a
provenance reference.

| Class | Role field | Description |
|---|---|---|
| `bootstrap-payload` | `bootloader` | First-stage loader chain (m1n1 → U-Boot for M2; UEFI stub for generic targets). Bootstraps the recovery kernel. |
| `recovery-payload` | `recovery-image` | Compressed recovery root filesystem image. Contains repair tooling and rollback scripts. |
| `installer-payload` | `installer-data` | Installer data bundle (Asahi-style JSON + asset pack for M2; EFI layout descriptor for UEFI targets). |
| `recovery-kernel` | `kernel` | Recovery-mode Linux kernel image. |
| `recovery-initrd` | `initrd` | Recovery initramfs. Contains early userspace for the recovery environment. |
| `manifest` | `manifest` | Signed artifact manifest listing all artifact digests for this build result. |
| `checksums` | `other` | Detached checksum file (`SHA256SUMS`) over all lane artifacts. |
| `sbom-reference` | `other` | SPDX or CycloneDX SBOM document reference for the full artifact set. |
| `provenance-reference` | `other` | SLSA / in-toto provenance bundle reference for the full artifact set. |

### Platform notes

- **M2 / Apple Silicon** is the first-class proof target. The `bootstrap-payload` is the
  m1n1 + U-Boot chain required by the Asahi boot picker.
- **uefi-x86_64 / uefi-aarch64** use a UEFI stub bootloader instead of m1n1. The
  `installer-payload` becomes a UEFI-layout descriptor rather than an Asahi-style JSON
  pack.
- **generic-arm64** uses U-Boot directly; the `bootstrap-payload` is a U-Boot binary.

The `platforms` field in the build-result fixture lists all targets a given lane run
covers.

## Build pipeline stages

```
1. Source fetch
   └─ Fetch pinned source refs for kernel, initrd, m1n1, U-Boot, recovery rootfs,
      installer data. Verify against NLBoot RC manifest hashes (execute=false).

2. Artifact build  [dry-run in fixtures; not executed]
   ├─ build bootstrap-payload  (m1n1 + U-Boot chain / UEFI stub)
   ├─ build recovery-kernel    (Linux kernel, recovery config)
   ├─ build recovery-initrd    (initramfs, recovery userspace)
   ├─ build recovery-payload   (recovery rootfs image)
   └─ build installer-payload  (Asahi installer JSON pack / UEFI layout)

3. Manifest assembly
   └─ Compute SHA-256 over each artifact → write manifest.json + SHA256SUMS

4. SBOM generation  [status: pending for NLBoot RC]
   └─ Generate SPDX/CycloneDX SBOM over build graph → sbom-reference artifact

5. Provenance attestation
   └─ Emit SLSA Build L2 + in-toto attestation → provenance-reference artifact

6. Signing
   └─ Sigstore/cosign sign manifest.json → sigstore bundle reference

7. Output
   └─ Produce ArtifactBuildLaneResult (dry-run fixture: recovery-installer-build-result.example.json)
   └─ Feed artifact URIs + SHA-256 digests into BootReleaseSet packaging step
```

All pipeline stages are side-effect-free in dry-run mode (`dryRun: true`,
`execute: false`). No artifacts are uploaded or published from a dry-run invocation.

## Checksums and content addressing

Each artifact in the lane output carries a `sha256` field. The `checksums` artifact
(`SHA256SUMS`) is a detached file that duplicates all digests in a standard format,
suitable for offline verification. Content-addressed URIs use the digest as part of the
path (e.g., `https://example.invalid/…/<sha256>/filename`).

> **Note**: In dry-run fixtures, all SHA-256 values are placeholder zeros. Real digests
> will be produced by the build system.

## SBOM references

Each artifact carries an `sbomRef` field pointing to an SPDX or CycloneDX SBOM
document. The lane also produces a top-level `sbomRef` covering the entire artifact set.
SBOM documents are not embedded in the build-result fixture; they are referenced by URI.

SBOM proof for NLBoot RC is in progress. Until it is complete, `sbomStatus` in the
fixture is `"pending"`.

## Provenance references

Each artifact carries a `provenanceRef` field pointing to an SLSA / in-toto attestation
bundle. The lane also emits a top-level `provenanceRef` for the full build. Provenance
bundles are not embedded in the build-result fixture; they are referenced by URI.

## Dry-run fixture

`examples/artifact-build-lane/recovery-installer-build-result.example.json` — a
minimal valid `ArtifactBuildLaneResult` dry-run fixture for the Recovery/Installer lane.
This fixture is syntax-checked by `make validate`.

## Output → BootReleaseSet mapping

Lane output feeds the packaging step (see
[`M2_RECOVERY_INSTALLER_PACKAGING.md`](M2_RECOVERY_INSTALLER_PACKAGING.md)):

| Lane artifact class | BootReleaseSet field |
|---|---|
| `bootstrap-payload` | `spec.artifacts[role=bootloader]` |
| `recovery-kernel` | `spec.artifacts[role=kernel]` |
| `recovery-initrd` | `spec.artifacts[role=initrd]` |
| `recovery-payload` | `spec.artifacts[role=recovery-image]` |
| `installer-payload` | `spec.artifacts[role=installer-data]` |
| `manifest` | `spec.artifacts[role=manifest]` |
| `checksums` / `sbom-reference` / `provenance-reference` | `spec.artifacts[role=other]` |
| `provenanceRef` (top-level) | `spec.provenance` |
| `sigstoreBundleRef` | `spec.signature.bundleRef` |

## Known gaps

- Real artifact build execution is not implemented; all digests in fixtures are
  placeholder zeros.
- SBOM proof for NLBoot RC is pending; `sbomStatus` is `"pending"` in fixtures.
- Sigstore signing of lane artifacts is not yet automated; `sigstoreBundleRef` in
  fixtures is a placeholder reference.
- Artifact content-addressed cache upload and SHA-256 fetch verification are not yet
  implemented.
- UEFI and generic-arm64 PAL notes are stubs; M2 is the only fully specified target for
  this milestone.

## References

- [`M2_RECOVERY_INSTALLER_PACKAGING.md`](M2_RECOVERY_INSTALLER_PACKAGING.md) — packaging step that consumes lane output
- [`NLBOOT_INTEGRATION.md`](NLBOOT_INTEGRATION.md) — nlboot adapter integration guide
- [`APPLE_SILICON_EVIDENCE_NORMALIZATION.md`](APPLE_SILICON_EVIDENCE_NORMALIZATION.md) — evidence normalization design
- [`WORLD_CLASS_TARGETS.md`](WORLD_CLASS_TARGETS.md) — implementation roadmap
- [`examples/artifact-build-lane/recovery-installer-build-result.example.json`](../examples/artifact-build-lane/recovery-installer-build-result.example.json) — dry-run build-result fixture
- [`SourceOS-Linux/sourceos-spec`](https://github.com/SourceOS-Linux/sourceos-spec) — canonical BootReleaseSet and ReleaseSet schemas (referenced, not duplicated)
- [`SociOS-Linux/nlboot`](https://github.com/SociOS-Linux/nlboot) — upstream safe planner (release-candidate; no tagged release yet)
