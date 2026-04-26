# nlboot Compatibility Mapping

`SociOS-Linux/nlboot` is no longer just a conceptual precursor. It already implements a safe planning core for SourceOS/SociOS boot and recovery.

## Upstream nlboot facts

The current nlboot reference implementation provides:

- `SignedBootManifest`
- `EnrollmentToken`
- `BootPlan`
- `nlboot-plan` CLI
- RSA-PSS/SHA-256 manifest verification
- FIPS-compatible crypto profile marker
- one-time enrollment token validation
- side-effect-free planning with `execute=false`

## SourceOS Boot integration stance

`sourceos-boot` should not fork the protocol vocabulary unnecessarily.

Instead:

- nlboot remains the safe planner/reference protocol lane.
- SourceOS Boot adapts nlboot manifest/token/plan concepts into BootReleaseSet v1 and Prophet Lattice evidence contracts.
- BootReleaseSet remains the platform handoff object for Prophet Platform, SourceOS, and Lattice.

## Field mapping

| nlboot field | SourceOS BootReleaseSet / adapter field |
|---|---|
| `manifest_id` | `provenance.sourceRefs[]` / evidence manifest identity |
| `boot_release_set_id` | `BootAuthorization.boot_release_set_ref` |
| `base_release_set_ref` | `spec.releaseSetRef` |
| `boot_mode` | `spec.channels[]` and boot evidence `bootMode` |
| `artifacts.kernel_ref` | artifact role `kernel` |
| `artifacts.initrd_ref` | artifact role `initrd` |
| `artifacts.rootfs_ref` | artifact role `rootfs` |
| `signature_ref` | `signature.bundleRef` |
| `signer_ref` | `provenance.builderId` for current safe-planner bridge |
| `signature_algorithm` | `signature.type` mapping and trust policy note |
| `crypto_profile` | policy/trust evidence note |
| `EnrollmentToken.token_id` | `BootAuthorization.token_id` |
| `EnrollmentToken.expires_at` | `BootAuthorization.expires_at` |
| `EnrollmentToken.boot_release_set_ref` | `BootAuthorization.boot_release_set_ref` |

## Current implementation

`src/sourceos_boot/adapter.py` includes:

- `NlbootManifestView`
- `NlbootTokenView`
- `authorization_from_nlboot_token`
- `boot_release_set_patch_from_nlboot_manifest`
- `build_evidence_from_nlboot_manifest`

These are pure, side-effect-free mappings suitable for CI and contract testing.

## Next implementation step

Wire the adapter to verified nlboot planner output:

1. Accept nlboot verified manifest document.
2. Accept nlboot enrollment token document.
3. Run nlboot verification/planning out-of-process or via library import.
4. Convert resulting manifest/token/plan into BootReleaseSet evidence.
5. Keep host mutation disabled until signed policy explicitly permits install/recovery actions.
