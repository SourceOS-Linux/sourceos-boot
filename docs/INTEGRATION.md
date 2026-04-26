# SourceOS Boot Integration Contract

`sourceos-boot` is consumed by the wider Prophet Lattice topology as the boot and recovery implementation boundary.

## Upstream dependencies

- `SourceOS-Linux/sourceos-spec`: canonical contract home. BootReleaseSet v0 starts here and should graduate upstream once stable.
- `SociOS-Linux/nlboot`: original bootstrap design input. This repo may depend on, vendor, or supersede pieces of nlboot after implementation review.
- Asahi-style Apple Silicon boot/install work: platform adaptation input for M2 demo.

## Downstream consumers

- `SocioProphet/prophet-platform`: release service, control-plane UI, device registration, policy assignment, evidence collection.
- `SocioProphet/lattice-forge`: runtime/image/kernel artifacts referenced by BootReleaseSet and installed ReleaseSets.
- `SocioProphet/agentplane`: agent-plane recovery and runtime repair flows after boot.
- `SocioProphet/sociosphere`: workspace topology and dependency-direction validation.

## Contract handoff

The handoff unit is `BootReleaseSet`.

A Prophet Platform release service should be able to:

1. ingest or reference a BootReleaseSet document;
2. verify artifact digests and signatures;
3. assign the BootReleaseSet to a device, group, project, or organization;
4. issue a time-bound enrollment token;
5. receive required evidence reports after live boot, install, repair, or rollback.

## Dependency direction

`sourceos-boot` may import schemas from `sourceos-spec` once published.

`sourceos-boot` must not import platform UI or long-running application service code from `prophet-platform`.

`prophet-platform` may consume generated schemas, examples, or release artifacts from `sourceos-boot`.

## Evidence reports

Minimum evidence reports for demo-grade integration:

- `device-claim`
- `manifest-hash`
- `verification-result`
- `selected-channel`
- `boot-mode`
- `install-result` or `rollback-result`

## Implementation milestones

1. Bootstrap manifest validation.
2. nlboot compatibility review.
3. enrollment-token exchange stub.
4. artifact digest verification.
5. Apple Silicon recovery PAL notes.
6. PC/Purism netboot PAL notes.
