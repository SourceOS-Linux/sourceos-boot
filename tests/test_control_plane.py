from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sourceos_boot.control_plane import ControlPlaneBootReleaseSetError, build_control_plane_boot_plan

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "control-plane-boot-release-set.example.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_builds_non_mutating_plan_from_control_plane_boot_release_set() -> None:
    plan = build_control_plane_boot_plan(load_fixture())

    payload = plan.to_dict()
    assert payload["boot_release_set_id"] == "urn:srcos:boot-release-set:m2-demo-recovery-2026-04-26"
    assert payload["base_release_set_ref"] == "urn:srcos:release-set:m2-demo-2026-04-26"
    assert payload["boot_mode"] == "recovery"
    assert payload["boot_channel"] == "rescue"
    assert payload["action"] == "plan-rescue"
    assert payload["execute"] is False
    assert payload["policy_ref"] == "urn:srcos:policy:boot-recovery-m2-demo-v1"
    assert payload["artifact_refs"]["manifest_ref"].startswith("urn:srcos:artifact:")
    assert payload["boot_capabilities"]["disk_write"] == "recovery-scoped"
    assert payload["boot_capabilities"]["kexec_allowed"] is False
    assert payload["offline_fallback"] == {
        "enabled": True,
        "strategy": "last-known-good-signed-boot-release-set",
        "requires_signature_verification": True,
        "allows_unsigned_artifacts": False,
    }
    assert payload["proof_reports"] == [
        "device-claim",
        "environment-fingerprint",
        "manifest-digest",
        "artifact-hash-manifest",
        "policy-decision",
        "rollback-result",
    ]
    assert payload["verification_gates"] == [
        "verify-boot-release-set-status-ready",
        "verify-manifest-signature",
        "verify-artifact-refs-present",
        "verify-policy-ref-present",
        "verify-proof-reporting-required",
        "verify-offline-fallback-signature-required",
        "verify-kexec-denied",
        "verify-disk-write-scope",
    ]


def test_rejects_unsigned_offline_fallback() -> None:
    doc = load_fixture()
    doc["offline_fallback"]["allows_unsigned_artifacts"] = True
    with pytest.raises(ControlPlaneBootReleaseSetError, match="must not allow unsigned artifacts"):
        build_control_plane_boot_plan(doc)


def test_rejects_non_ready_boot_release_set() -> None:
    doc = load_fixture()
    doc["status"] = "draft"
    with pytest.raises(ControlPlaneBootReleaseSetError, match="status must be ready"):
        build_control_plane_boot_plan(doc)


def test_cli_plans_control_plane_boot_release_set() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sourceos_boot.cli",
            "plan-control-plane",
            "--boot-release-set",
            str(FIXTURE),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["kind"] == "ControlPlaneBootPlan"
    assert payload["plan"]["action"] == "plan-rescue"
    assert payload["plan"]["execute"] is False
