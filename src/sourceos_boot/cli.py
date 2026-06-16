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
from .control_plane import build_control_plane_boot_plan
from .rollback_executor import RollbackExecutor


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
    boot_release_set_doc = load_json(args.boot_release_set)
    plan = build_control_plane_boot_plan(boot_release_set_doc)
    output = {
        "apiVersion": "sourceos.dev/v1",
        "kind": "ControlPlaneBootPlan",
        "plan": plan.to_dict(),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def rollback_plan(args: argparse.Namespace) -> int:
    chain_info = AsahiBootChainInfo(
        chain_type=BOOT_CHAIN_TYPE,
        m1n1_version=None,
        uboot_version=None,
        efi_vars_mutable=False,
    )
    chain = AsahiBootChain(chain_info=chain_info)
    plan = chain.plan_rollback()
    print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    return 0 if plan.allowed else 2


def rollback_execute(args: argparse.Namespace) -> int:
    chain_info = AsahiBootChainInfo(
        chain_type=BOOT_CHAIN_TYPE,
        m1n1_version=None,
        uboot_version=None,
        efi_vars_mutable=False,
    )
    chain = AsahiBootChain(chain_info=chain_info)
    plan = chain.plan_rollback()
    executor = RollbackExecutor(timeout_s=args.timeout)
    result = executor.execute(plan, dry_run=not args.execute)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SourceOS Boot helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    plan = subparsers.add_parser(
        "plan-control-plane",
        help="Build a safe, non-mutating boot plan from a sourceos-spec control-plane BootReleaseSet",
    )
    plan.add_argument("--boot-release-set", type=Path, required=True)
    plan.set_defaults(func=plan_control_plane)

    rollback = subparsers.add_parser("rollback", help="NixOS generation rollback planning and execution")
    rollback_sub = rollback.add_subparsers(dest="rollback_command", required=True)

    rp = rollback_sub.add_parser("plan", help="emit a non-mutating AsahiRollbackPlan (no changes)")
    rp.set_defaults(func=rollback_plan)

    rx = rollback_sub.add_parser("execute", help="execute the rollback plan (dry-run unless --execute)")
    rx.add_argument("--execute", action="store_true", help="actually run nixos-rebuild --rollback (default: dry-run)")
    rx.add_argument("--timeout", type=int, default=300, help="subprocess timeout in seconds (default: 300)")
    rx.set_defaults(func=rollback_execute)

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
