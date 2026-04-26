# SourceOS Boot

SourceOS Boot is the boot, recovery, and secure live-provisioning surface for SourceOS.

It evolves the original `SociOS-Linux/nlboot` concept into a SourceOS-native **BootReleaseSet** model that can support:

- secure live boot and remote install semantics;
- recovery and rollback entries for SourceOS systems;
- M2/Apple Silicon boot integration through the Asahi-style platform adaptation layer;
- PC/Purism/generic hardware netboot semantics through UEFI/iPXE-like or small-bootstrap-media flows;
- local-first device registration and later local-mesh/cloud-mesh replication;
- integration with Prophet Lattice, SourceOS specs, AgentPlane, and Lattice Forge.

## Product role

**Lattice is the control plane. SourceOS is the substrate. Fog is where execution happens.**

`sourceos-boot` owns the boot/recovery implementation boundary:

```text
BootReleaseSet -> boot manifest -> recovery/live environment -> verified install/update/rollback
```

It does **not** own the whole OS image, package/runtime distribution, or platform dashboard. Those remain in their respective repos:

- `SourceOS-Linux/sourceos-spec` — canonical schemas/contracts.
- `SocioProphet/lattice-forge` — governed runtimes, kernels, packages, images, SBOMs.
- `SocioProphet/prophet-platform` — platform services and control-plane UI.
- `SocioProphet/agentplane` — governed execution and replay.
- `SociOS-Linux/nlboot` — original bootstrap primitive and design input.

## Initial implementation

This repo currently provides:

- `schemas/boot-release-set.schema.json` — BootReleaseSet v0 contract.
- `examples/boot-release-set.example.json` — minimal valid example.
- `src/sourceos_boot/validate_boot_release_set.py` — zero-dependency validator for examples and CI.
- `.github/workflows/ci.yml` — validation workflow.

## Near-term roadmap

1. Align BootReleaseSet v0 with `sourceos-spec` once the shared schema family lands.
2. Add the nlboot compatibility adapter: announce, authorize, fetch manifest, verify, kexec/install/recover.
3. Add Apple Silicon PAL notes and implementation stubs for SourceOS Recovery Environment.
4. Add UEFI/iPXE bootstrap profile for PC/Purism class hardware.
5. Emit evidence records: device claim, manifest hash, boot mode, selected ReleaseSet, verification result.
