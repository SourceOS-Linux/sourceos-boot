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

## Prophet Trust Chain boot verification evidence

SourceOS Boot owns the boot/device verification slice of Prophet Trust Chain. The platform standard and admission contract live in `SocioProphet/prophet-platform`:

- `docs/standards/PROPHET_TRUST_CHAIN_V0.md`
- `docs/TRUST_CHAIN_ADMISSION_CONTRACT.md`
- `docs/standards/PROPHET_TRUST_CHAIN_IMPLEMENTATION_MAP.md`

This repo now carries `TrustChainBootVerificationEvidence`, which binds a `BootReleaseSet` to device claim, manifest hash, selected release set, boot mode, verification result, rollback/recovery posture, Trust Chain admission refs, and boot/install/rollback effects.

Relevant files:

- `schemas/trust-chain-boot-verification-evidence.v0.1.schema.json`
- `examples/trust-chain-boot-verification.valid.json`
- `examples/trust-chain-boot-verification.blocked.json`
- `src/sourceos_boot/validate_trust_chain_boot_verification.py`
- `tests/test_trust_chain_boot_verification.py`

Validation:

```bash
make validate-trust-chain-boot-verification
python -m pytest tests/test_trust_chain_boot_verification.py
```

The valid fixture requires verified device claim, manifest hash, selected release set, boot mode, passing verification result, attestation ref, rollback/recovery posture, policy profile, admission decision, and runtime receipt before boot/install admission is allowed.

The blocked fixture proves fail-closed behavior when device claim and manifest verification evidence are missing. Boot/install are denied, rollback remains allowed, and remediation authority is preserved.

Boundary: SourceOS Boot records boot/device verification evidence. It does not certify production hardware by itself, mutate live boot entries in this tranche, own package/runtime distribution, replace Lattice Forge runtime evidence, replace Policy Fabric policy profiles, replace AgentPlane execution evidence, or replace Prophet Platform admission composition.

## Initial implementation

This repo currently provides:

- `schemas/boot-release-set.schema.json` — BootReleaseSet v0 contract.
- `examples/boot-release-set.example.json` — minimal valid example.
- `src/sourceos_boot/validate_boot_release_set.py` — zero-dependency validator for examples and CI.
- `schemas/trust-chain-boot-verification-evidence.v0.1.schema.json` — Trust Chain boot/device verification evidence contract.
- `examples/trust-chain-boot-verification.valid.json` — valid boot/device verification evidence example.
- `examples/trust-chain-boot-verification.blocked.json` — fail-closed boot/device verification evidence example.
- `src/sourceos_boot/validate_trust_chain_boot_verification.py` — Trust Chain boot verification validator.
- `.github/workflows/ci.yml` — validation workflow.

### A/B dual-slot update

Reference implementation of the `sourceos-spec` A/B fallback update contract
v0.1 — the estate's first update path with a guaranteed way back.

- `src/sourceos_boot/ab_update_machine.py` — the state machine. Explicit
  allowed-transition table, fail-closed admission, GPT-style attempt budget, and
  the rollback paths. Zero-dependency, pure stdlib, side-effect free.
- `schemas/ab-update/` — the three contract schemas, vendored from
  `sourceos-spec` (see that directory's README; never edit them here).
- `examples/ab-update/` — a slot pair, a health probe, and two event sequences
  (one settling `refused`, one `promoted`).
- `tests/test_ab_update_machine.py`, `tests/test_ab_update_conformance.py` —
  the transitions and rollback paths, plus validation of every emitted document
  against the vendored schemas.

The invariant it exists for: **the currently-good slot is never overwritten by
the update being applied.** The write target is derived from the current slot
roles inside the machine — there is no argument by which a caller could aim an
update at the slot the target falls back to — and `InvariantViolation` is raised,
not returned, if the preserved payload ever changes.

```sh
# Plan an update: opens a transaction against the non-active slot. Pure.
sourceos-boot ab-update plan \
  --slots examples/ab-update/slots.example.json \
  --probe examples/ab-update/health-probe.example.json \
  --transaction-id urn:srcos:update-transaction:fog_edge_07_0042 \
  --payload-digest sha256:<digest> --version 2026.07.4

# Rollback drill: replay recorded events, emit the settled contract documents.
sourceos-boot ab-update replay \
  --slots examples/ab-update/slots.example.json \
  --probe examples/ab-update/health-probe.example.json \
  --transaction-id urn:srcos:update-transaction:fog_edge_07_0042 \
  --events examples/ab-update/events.refused.example.json
```

Both subcommands recompute the health probe's `definitionDigest` from its own
checks and refuse a stale one, so a gate cannot be relaxed after it is pinned
without the command noticing.

**Not implemented here:** the GRUB slot selector and the A/B partition layout.
Those live in `source-os` and are not verifiable without booting a machine.

## Near-term roadmap

1. Align BootReleaseSet v0 with `sourceos-spec` once the shared schema family lands.
2. Add the nlboot compatibility adapter: announce, authorize, fetch manifest, verify, kexec/install/recover.
3. Add Apple Silicon PAL notes and implementation stubs for SourceOS Recovery Environment.
4. Add UEFI/iPXE bootstrap profile for PC/Purism class hardware.
5. Emit evidence records: device claim, manifest hash, boot mode, selected ReleaseSet, verification result.
6. Bind boot/device verification evidence into Prophet Platform admission responses and AgentPlane runtime receipts.
