# Vendored: A/B fallback update contract v0.1

These three schemas are **copies**, not originals. The authoritative source is
`SourceOS-Linux/sourceos-spec`:

| File | Upstream path |
| --- | --- |
| `UpdateSlot.json` | `schemas/UpdateSlot.json` |
| `UpdateTransaction.json` | `schemas/UpdateTransaction.json` |
| `UpdateHealthProbe.json` | `schemas/UpdateHealthProbe.json` |

Normative notes: `specs/ab-fallback-update-contract.md` in that repo.

Vendored at `sourceos-spec` commit `cfac3c91adc02244a8ef9c16295784b73f523632`
(PR SourceOS-Linux/sourceos-spec#212).

## Why vendored rather than imported

No repo in this estate imports another repo's package; coupling is by JSON
contract. Vendoring the schemas lets `tests/test_ab_update_conformance.py`
validate this repo's own emitted documents against the real contract in CI,
which is the difference between claiming conformance and demonstrating it.

## Editing these is always wrong

Change the contract upstream and re-vendor. A local edit here produces a build
that passes against a contract nobody else holds — the schema equivalent of a
green test suite pinned to a fork.
