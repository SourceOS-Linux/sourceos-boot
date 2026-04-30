# NLBoot Integration Guide

`sourceos-boot` adapts the [`SociOS-Linux/nlboot`](https://github.com/SociOS-Linux/nlboot) safe planning protocol into SourceOS BootReleaseSet contracts and Prophet Lattice evidence envelopes.

## What nlboot provides

The nlboot reference implementation defines:

| Object | Description |
|---|---|
| `SignedBootManifest` | Signed artifact manifest with kernel/initrd/rootfs refs, boot mode, and crypto profile |
| `EnrollmentToken` | One-time, time-bound, boot-mode-scoped authorization token |
| `BootPlan` | Side-effect-free plan record with `execute=false` and required proof gates |

nlboot enforces RSA-PSS/SHA-256 manifest verification and FIPS-140-3-compatible crypto profile markers before emitting any plan.

## Integration stance

`sourceos-boot` does not fork the nlboot protocol vocabulary. Instead:

- nlboot remains the safe planner and reference protocol lane.
- `sourceos-boot` adapts nlboot manifest/token/plan output into BootReleaseSet patches, BootAuthorization records, and evidence envelopes.
- BootReleaseSet is the handoff object for Prophet Platform, SourceOS, and Lattice.

See [`NLBOOT_COMPATIBILITY.md`](NLBOOT_COMPATIBILITY.md) for the field-level mapping.

## Adapter flow

The `SourceOSBootAdapter` models a five-stage, side-effect-free handshake:

```
announce → authorize → fetch manifest → verify → emit evidence
```

1. **Announce** — build a `SourceOSBootAnnounce` payload from a `DeviceClaim`.
2. **Authorize** — convert an nlboot `EnrollmentToken` document into a `BootAuthorization`.
3. **Fetch** — build a `SourceOSBootFetchRequest` using the `BootAuthorization`.
4. **Verify** — apply the nlboot manifest to produce a `BootReleaseSet` patch.
5. **Evidence** — emit a `BootEvidence` envelope covering device claim, manifest hash, channel, boot mode, and verification result.

All operations are pure and side-effect-free. No disk writes, kexec calls, or network requests are performed.

## Boot mode mapping

| nlboot `boot_mode` | SourceOS channel | SourceOS action |
|---|---|---|
| `installer` | `installer` | `install` |
| `recovery` | `recovery` | `repair` |
| `ephemeral` | `live` | `kexec` |
| `bootstrap` | `live` | `enroll` |

## CLI usage

```bash
PYTHONPATH=src python -m sourceos_boot.cli adapt-nlboot \
  --manifest examples/nlboot/manifest.json \
  --token   examples/nlboot/token.json \
  --device-id             device-demo-m2 \
  --public-key-fingerprint sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  --platform              apple-silicon \
  --nonce                 nonce-demo-1 \
  --correlation-id        corr-demo-1
```

The command emits an `NlbootAdapterOutput` JSON object to stdout. See
`examples/nlboot/adapted-output.example.json` for the expected structure.

## Fixture files

| File | Description |
|---|---|
| `examples/nlboot/manifest.json` | Minimal nlboot `SignedBootManifest` fixture |
| `examples/nlboot/token.json` | Minimal nlboot `EnrollmentToken` fixture |
| `examples/nlboot/adapted-output.example.json` | Expected `NlbootAdapterOutput` for the above fixtures |

## Validation

```bash
make validate   # validate BootReleaseSet example and exercise the nlboot adapter
make test       # run pytest suite
```

The CI workflow (`ci.yml`) also validates `examples/boot-release-set.example.json`
and runs the full pytest suite on every push and pull request.

## Known gaps

- Real nlboot manifest signature verification (RSA-PSS/SHA-256) is not yet wired
  into this adapter; the adapter accepts a pre-verified manifest document.
- Artifact fetch, SHA-256 check, and content-addressed cache are implemented in
  the Rust `nlboot-client` lane; the Python adapter here covers planning only.
- Host mutation (kexec, install, rollback) remains disabled pending explicit
  platform adapter review. See `WORLD_CLASS_TARGETS.md` for the implementation path.

## References

- [`NLBOOT_COMPATIBILITY.md`](NLBOOT_COMPATIBILITY.md) — detailed field mapping
- [`INTEGRATION.md`](INTEGRATION.md) — upstream/downstream dependency contract
- [`WORLD_CLASS_TARGETS.md`](WORLD_CLASS_TARGETS.md) — implementation roadmap
- [`SociOS-Linux/nlboot`](https://github.com/SociOS-Linux/nlboot) — upstream safe planner
- [`src/sourceos_boot/adapter.py`](../src/sourceos_boot/adapter.py) — adapter implementation
