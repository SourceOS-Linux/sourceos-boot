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
from .control_plane import build_control_plane_boot_plan


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
