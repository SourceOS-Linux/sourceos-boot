#!/usr/bin/env python3
"""SourceOS Boot command-line helpers."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .adapter import DeviceClaim, SourceOSBootAdapter
from .asahi_boot_chain import AsahiBootChain, AsahiBootChainInfo, BOOT_CHAIN_TYPE


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def canonical_json_sha256(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def adapt_nlboot(args: argparse.Namespace) -> int:
    manifest_doc = load_json(args.manifest)
    token_doc = load_json(args.token)
    adapter = SourceOSBootAdapter()

    claim = DeviceClaim(
        device_id=args.device_id,
        public_key_fingerprint=args.public_key_fingerprint,
        platform=args.platform,
        nonce=args.nonce,
    )
    authorization = adapter.authorization_from_nlboot_token(token_doc, correlation_id=args.correlation_id)
    patch = adapter.boot_release_set_patch_from_nlboot_manifest(manifest_doc)
    evidence = adapter.build_evidence_from_nlboot_manifest(
        claim=claim,
        authorization=authorization,
        manifest_doc=manifest_doc,
        manifest_hash=canonical_json_sha256(manifest_doc),
        verification_result=args.verification_result,
    )

    output = {
        "apiVersion": "sourceos.dev/v1",
        "kind": "NlbootAdapterOutput",
        "authorization": authorization.to_dict(),
        "bootReleaseSetPatch": patch,
        "evidence": evidence.to_dict(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def plan_control_plane(args: argparse.Namespace) -> int:
    """Render a ControlPlaneBootPlan from a BootReleaseSet document.

    build_control_plane_boot_plan() existed in control_plane.py and was reachable from
    nothing: the subcommand its test invokes was never registered, so `plan-control-plane`
    exited 2 with "invalid choice". The planner was written; the door to it was not.

    `execute` is stamped False here rather than in the dataclass because it is a property
    of THIS invocation, not of the plan: every stage in this adapter is pure and
    side-effect-free, and a planning call that could ever report execute=true would be a
    different thing wearing the same name.
    """
    from sourceos_boot.control_plane import build_control_plane_boot_plan

    doc = load_json(args.boot_release_set)
    plan = build_control_plane_boot_plan(doc)
    output = {
        "apiVersion": "sourceos.dev/v1",
        "kind": "ControlPlaneBootPlan",
        "plan": {**plan.to_dict(), "execute": False},
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


_AB_UPDATE_EVENTS = {
    "begin": lambda m, e: m.begin(
        e["payloadDigest"], payload_ref=e.get("payloadRef"), version=e.get("version")
    ),
    "write_complete": lambda m, e: m.write_complete(
        e.get("observedDigest") or e["payloadDigest"]
    ),
    "write_failed": lambda m, e: m.write_failed(e.get("detail", "write failed")),
    "arm": lambda m, e: m.arm(),
    "boot_attempt": lambda m, e: m.boot_attempt(),
    "probe_pass": lambda m, e: m.probe_pass(e.get("detail")),
    "probe_fail": lambda m, e: m.probe_fail(e.get("detail", "health probe failed")),
    "watchdog_expired": lambda m, e: m.watchdog_expired(
        e.get("detail", "watchdog expired"),
        expiry_action=e.get("expiryAction", "reboot"),
    ),
    "operator_abort": lambda m, e: m.operator_abort(e.get("detail", "aborted by operator")),
}


def _load_ab_update_machine(args: argparse.Namespace):
    """Build a machine from a slot-pair document and a pinned health probe.

    The probe's definitionDigest is RECOMPUTED here, not read back. If the gate
    has been edited since it was authored, this refuses rather than pinning a
    digest that no longer describes the checks — which is the one thing standing
    between a failing candidate and promotion through a relaxed gate.
    """
    from sourceos_boot.ab_update_machine import (
        machine_from_slot_documents,
        probe_definition_digest,
    )

    slot_docs = json.loads(args.slots.read_text(encoding="utf-8"))
    if not isinstance(slot_docs, list):
        raise ValueError(f"expected a JSON array of two UpdateSlot documents in {args.slots}")

    probe = load_json(args.probe)
    recomputed = probe_definition_digest(probe)
    if probe.get("definitionDigest") != recomputed:
        raise ValueError(
            f"health probe {probe.get('id')} carries a stale definitionDigest: "
            f"recorded {probe.get('definitionDigest')}, but its checks hash to "
            f"{recomputed}. The gate was edited after it was pinned; re-pin it "
            "deliberately rather than promoting through a gate nobody re-read."
        )

    return machine_from_slot_documents(
        slot_docs,
        transaction_id=args.transaction_id,
        health_probe_ref=probe["id"],
        health_probe_digest=recomputed,
        max_attempts=args.max_attempts,
    )


def ab_update_plan(args: argparse.Namespace) -> int:
    """Plan an A/B update: open a transaction against the non-active slot.

    Pure and side-effect-free, like plan-control-plane. The write target is
    derived from the current roles inside the machine, so there is no argument on
    this command by which a caller could aim an update at the slot the target
    falls back to. `execute` is stamped False for the same reason it is on the
    control-plane plan: this call plans, and a planning call that could ever
    report execute=true would be a different thing wearing the same name.
    """
    machine = _load_ab_update_machine(args)
    result = machine.begin(
        args.payload_digest, payload_ref=args.payload_ref, version=args.version
    )
    if not result.allowed:
        print(f"sourceos-boot: {result.reason}", file=sys.stderr)
        return 2

    output = {
        "apiVersion": "sourceos.dev/v1",
        "kind": "AbUpdatePlan",
        "execute": False,
        "preservedSlot": machine._from_slot,
        "writeTarget": machine._to_slot,
        "transaction": machine.to_transaction_document(),
        "plannedSlots": machine.to_slot_documents(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def ab_update_replay(args: argparse.Namespace) -> int:
    """Replay a recorded event sequence through the update state machine.

    This is the rollback-drill surface: it settles a transaction from recorded
    events and emits the spec-conformant UpdateTransaction and UpdateSlot
    documents, so a fallback path can be exercised and evidenced without the
    hardware that produced the events. Refused events are reported in the output
    rather than dropped — an event the machine declined is exactly what a drill
    is looking for.
    """
    machine = _load_ab_update_machine(args)
    events = json.loads(args.events.read_text(encoding="utf-8"))
    if not isinstance(events, list):
        raise ValueError(f"expected a JSON array of events in {args.events}")

    log = []
    for index, event in enumerate(events):
        name = event.get("event")
        handler = _AB_UPDATE_EVENTS.get(name)
        if handler is None:
            raise ValueError(f"event {index}: unknown event {name!r}")
        result = handler(machine, event)
        log.append(
            {
                "event": result.event,
                "allowed": result.allowed,
                "fromPhase": result.from_phase,
                "toPhase": result.to_phase,
                "reason": result.reason,
                "fellBackTo": result.fell_back_to,
            }
        )

    output = {
        "apiVersion": "sourceos.dev/v1",
        "kind": "AbUpdateReplay",
        "execute": False,
        "finalPhase": machine.phase,
        "transitions": log,
        "transaction": machine.to_transaction_document(),
        "slots": machine.to_slot_documents(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _asahi_chain() -> AsahiBootChain:
    """The chain the rollback path plans against.

    Restored from bc6dd8c unchanged: rollback is a NixOS-generation operation, so the
    chain metadata it needs is only the type — m1n1/u-boot versions are irrelevant to
    `nixos-rebuild --rollback` and EFI vars are not touched.
    """
    return AsahiBootChain(
        chain_info=AsahiBootChainInfo(
            chain_type=BOOT_CHAIN_TYPE,
            m1n1_version=None,
            uboot_version=None,
            efi_vars_mutable=False,
        )
    )


def rollback_plan(args: argparse.Namespace) -> int:
    plan = _asahi_chain().plan_rollback()
    print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    return 0 if plan.allowed else 2


def rollback_execute(args: argparse.Namespace) -> int:
    plan = _asahi_chain().plan_rollback()
    executor = RollbackExecutor(timeout_s=args.timeout)
    result = executor.execute(plan, dry_run=not args.execute)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SourceOS Boot helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── rollback ────────────────────────────────────────────────────────────────────
    # RESTORED. This subcommand existed at bc6dd8c ("feat(rollback): RollbackExecutor and
    # CLI execute path", #28) and was dropped by 1f679d2 (#48) while the executor library
    # it fronts stayed. source-os/modules/nixos/sourceos-syncd/default.nix invokes
    # `sourceos-boot rollback execute --execute` under `rollbackOnFailure`, so on main this
    # became argparse "invalid choice: 'rollback'" (exit 2) — swallowed by a trailing
    # `|| true`. Auto-rollback survived only because source-os/flake.lock pins
    # sourceos-boot-src to bc6dd8c, the commit immediately before the removal; the next
    # `nix flake update` would have silently disarmed it on stable-x86_64, canary-x86_64,
    # exit-x86_64 and builder-aarch64 — all of which set rollbackOnFailure = true.
    #
    # Nothing caught it: packages/sourceos-boot/default.nix sets doCheck = false, and its
    # pythonImportsCheck imports `sourceos_boot.rollback_executor` as a MODULE — which still
    # exists — so the package built green while its CLI surface had a hole. Proving a module
    # imports can never prove a subcommand parses; see tests/test_cli_rollback.py.
    rollback = subparsers.add_parser("rollback", help="NixOS generation rollback planning and execution")
    rollback_sub = rollback.add_subparsers(dest="rollback_command", required=True)

    rp = rollback_sub.add_parser("plan", help="emit a non-mutating AsahiRollbackPlan (no changes)")
    rp.set_defaults(func=rollback_plan)

    rx = rollback_sub.add_parser("execute", help="execute the rollback plan (dry-run unless --execute)")
    rx.add_argument("--execute", action="store_true", help="actually run nixos-rebuild --rollback (default: dry-run)")
    rx.add_argument("--timeout", type=int, default=300, help="seconds to allow nixos-rebuild (default: 300)")
    rx.set_defaults(func=rollback_execute)

    adapt = subparsers.add_parser("adapt-nlboot", help="Convert nlboot manifest/token JSON into SourceOS handoff objects")
    adapt.add_argument("--manifest", type=Path, required=True)
    adapt.add_argument("--token", type=Path, required=True)
    adapt.add_argument("--device-id", required=True)
    adapt.add_argument("--public-key-fingerprint", required=True)
    adapt.add_argument("--platform", required=True)
    adapt.add_argument("--nonce", required=True)
    adapt.add_argument("--correlation-id", required=True)
    adapt.add_argument("--verification-result", choices=["pass", "fail", "unknown"], default="pass")
    adapt.set_defaults(func=adapt_nlboot)

    plan_cp = subparsers.add_parser("plan-control-plane", help="Plan a control-plane boot from a BootReleaseSet")
    plan_cp.add_argument("--boot-release-set", type=Path, required=True)
    plan_cp.set_defaults(func=plan_control_plane)

    ab = subparsers.add_parser(
        "ab-update",
        help="A/B dual-slot update planning and rollback-drill replay",
    )
    ab_sub = ab.add_subparsers(dest="ab_command", required=True)

    def _common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--slots", type=Path, required=True,
                         help="JSON array of the target's two UpdateSlot documents")
        sub.add_argument("--probe", type=Path, required=True,
                         help="UpdateHealthProbe document gating promotion")
        sub.add_argument("--transaction-id", required=True,
                         help="URN for the transaction (urn:srcos:update-transaction:<id>)")
        sub.add_argument("--max-attempts", type=int, default=2,
                         help="Boot attempts before the candidate is refused (1..7, default 2)")

    ab_plan = ab_sub.add_parser("plan", help="Open a transaction against the non-active slot")
    _common(ab_plan)
    ab_plan.add_argument("--payload-digest", required=True)
    ab_plan.add_argument("--payload-ref", default=None)
    ab_plan.add_argument("--version", default=None)
    ab_plan.set_defaults(func=ab_update_plan)

    ab_replay = ab_sub.add_parser(
        "replay", help="Replay recorded update events and emit the settled contract documents"
    )
    _common(ab_replay)
    ab_replay.add_argument("--events", type=Path, required=True,
                           help="JSON array of update events to drive the machine")
    ab_replay.set_defaults(func=ab_update_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"sourceos-boot: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
