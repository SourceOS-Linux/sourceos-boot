"""Control-plane BootReleaseSet planner.

This module consumes the canonical SourceOS control-plane BootReleaseSet shape
from `SourceOS-Linux/sourceos-spec` and turns it into a safe, non-mutating boot
plan for sourceos-boot.

It deliberately performs no network, disk, kexec, install, rollback, or key
rotation side effects. It validates the boot intent and emits the operations a
future executor would need to perform, together with the proof reports required
by policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SAFE_DIGEST_PREFIXES = ("sha256:", "sha384:", "sha512:")
BOOT_ACTION_BY_CHANNEL = {
    "live": "boot-live-environment",
    "installer": "plan-install",
    "rescue": "plan-rescue",
    "rollback": "plan-rollback",
    "bootstrap": "plan-bootstrap",
}


@dataclass(frozen=True)
class ControlPlaneBootPlan:
    """Side-effect-free plan derived from a control-plane BootReleaseSet."""

    boot_release_set_id: str
    base_release_set_ref: str
    boot_mode: str
    boot_channel: str
    action: str
    status: str
    policy_ref: str
    platform_entrypoints: list[dict[str, Any]]
    artifact_refs: dict[str, str | None]
    signing: dict[str, Any]
    boot_capabilities: dict[str, Any]
    proof_reports: list[str]
    offline_fallback: dict[str, Any]
    verification_gates: list[str]
    execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "boot_release_set_id": self.boot_release_set_id,
            "base_release_set_ref": self.base_release_set_ref,
            "boot_mode": self.boot_mode,
            "boot_channel": self.boot_channel,
            "action": self.action,
            "status": self.status,
            "policy_ref": self.policy_ref,
            "platform_entrypoints": self.platform_entrypoints,
            "artifact_refs": self.artifact_refs,
            "signing": self.signing,
            "boot_capabilities": self.boot_capabilities,
            "proof_reports": self.proof_reports,
            "offline_fallback": self.offline_fallback,
            "verification_gates": self.verification_gates,
            "execute": self.execute,
        }


class ControlPlaneBootReleaseSetError(ValueError):
    """Raised when a control-plane BootReleaseSet is unsafe or malformed."""


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ControlPlaneBootReleaseSetError(f"{key} must be a non-empty string")
    return value


def _require_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ControlPlaneBootReleaseSetError(f"{key} must be an object")
    return value


def _require_list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ControlPlaneBootReleaseSetError(f"{key} must be a list")
    return value


def _artifact_refs(artifacts: dict[str, Any]) -> dict[str, str | None]:
    required = ["manifest_ref", "kernel_ref", "initrd_ref", "rootfs_ref"]
    refs: dict[str, str | None] = {}
    for key in required:
        refs[key] = _require_str(artifacts, key)
    for key in ["bootloader_ref", "installer_metadata_ref"]:
        value = artifacts.get(key)
        if value is not None and not isinstance(value, str):
            raise ControlPlaneBootReleaseSetError(f"artifacts.{key} must be a string or null")
        refs[key] = value
    return refs


def _validate_signing(signing: dict[str, Any]) -> None:
    signature_ref = _require_str(signing, "signature_ref")
    signer_ref = _require_str(signing, "signer_ref")
    signature_algorithm = _require_str(signing, "signature_algorithm")
    manifest_digest = _require_dict(signing, "manifest_digest")
    digest_value = _require_str(manifest_digest, "value")
    if not signature_ref.startswith("urn:srcos:signature:"):
        raise ControlPlaneBootReleaseSetError("signing.signature_ref must be a SourceOS signature URN")
    if not signer_ref.startswith("urn:srcos:key:"):
        raise ControlPlaneBootReleaseSetError("signing.signer_ref must be a SourceOS key URN")
    if signature_algorithm not in {"rsa-pss-sha256", "ed25519", "ecdsa-p256-sha256"}:
        raise ControlPlaneBootReleaseSetError("signing.signature_algorithm is unsupported")
    if not digest_value.startswith(SAFE_DIGEST_PREFIXES):
        raise ControlPlaneBootReleaseSetError("signing.manifest_digest.value must be sha256/sha384/sha512 prefixed")


def _verification_gates(plan: ControlPlaneBootPlan) -> list[str]:
    gates = [
        "verify-boot-release-set-status-ready",
        "verify-manifest-signature",
        "verify-artifact-refs-present",
        "verify-policy-ref-present",
        "verify-proof-reporting-required",
    ]
    if plan.offline_fallback.get("enabled"):
        gates.append("verify-offline-fallback-signature-required")
    if plan.boot_capabilities.get("kexec_allowed") is False:
        gates.append("verify-kexec-denied")
    if plan.boot_capabilities.get("disk_write") in {"installer-scoped", "recovery-scoped"}:
        gates.append("verify-disk-write-scope")
    return gates


def build_control_plane_boot_plan(doc: dict[str, Any]) -> ControlPlaneBootPlan:
    """Build a side-effect-free plan from canonical control-plane BootReleaseSet JSON."""

    boot_release_set_id = _require_str(doc, "boot_release_set_id")
    base_release_set_ref = _require_str(doc, "base_release_set_ref")
    boot_mode = _require_str(doc, "boot_mode")
    boot_channel = _require_str(doc, "boot_channel")
    status = _require_str(doc, "status")
    policy_ref = _require_str(doc, "policy_ref")

    if status != "ready":
        raise ControlPlaneBootReleaseSetError("BootReleaseSet status must be ready before planning")
    if boot_channel not in BOOT_ACTION_BY_CHANNEL:
        raise ControlPlaneBootReleaseSetError(f"unsupported boot_channel={boot_channel!r}")
    if boot_mode not in {"installer", "recovery", "ephemeral", "bootstrap"}:
        raise ControlPlaneBootReleaseSetError(f"unsupported boot_mode={boot_mode!r}")
    if not boot_release_set_id.startswith("urn:srcos:boot-release-set:"):
        raise ControlPlaneBootReleaseSetError("boot_release_set_id must be a SourceOS BootReleaseSet URN")
    if not base_release_set_ref.startswith("urn:srcos:release-set:"):
        raise ControlPlaneBootReleaseSetError("base_release_set_ref must be a SourceOS ReleaseSet URN")
    if not policy_ref.startswith("urn:srcos:policy:"):
        raise ControlPlaneBootReleaseSetError("policy_ref must be a SourceOS policy URN")

    platform_entrypoints = _require_list(doc, "platform_entrypoints")
    if not platform_entrypoints:
        raise ControlPlaneBootReleaseSetError("platform_entrypoints must not be empty")
    for index, entrypoint in enumerate(platform_entrypoints):
        if not isinstance(entrypoint, dict):
            raise ControlPlaneBootReleaseSetError(f"platform_entrypoints[{index}] must be an object")
        _require_str(entrypoint, "platform")
        _require_str(entrypoint, "entrypoint_kind")
        _require_str(entrypoint, "entrypoint_ref")

    artifacts = _artifact_refs(_require_dict(doc, "artifacts"))
    signing = _require_dict(doc, "signing")
    _validate_signing(signing)

    boot_capabilities = _require_dict(doc, "boot_capabilities")
    offline_fallback = _require_dict(doc, "offline_fallback")
    proof_reporting = _require_dict(doc, "proof_reporting")
    proof_reports = proof_reporting.get("reports")
    if proof_reporting.get("required") is not True:
        raise ControlPlaneBootReleaseSetError("proof_reporting.required must be true")
    if not isinstance(proof_reports, list) or not proof_reports:
        raise ControlPlaneBootReleaseSetError("proof_reporting.reports must be a non-empty list")
    if offline_fallback.get("enabled") and offline_fallback.get("requires_signature_verification") is not True:
        raise ControlPlaneBootReleaseSetError("enabled offline fallback must require signature verification")
    if offline_fallback.get("allows_unsigned_artifacts") is not False:
        raise ControlPlaneBootReleaseSetError("offline fallback must not allow unsigned artifacts")

    plan = ControlPlaneBootPlan(
        boot_release_set_id=boot_release_set_id,
        base_release_set_ref=base_release_set_ref,
        boot_mode=boot_mode,
        boot_channel=boot_channel,
        action=BOOT_ACTION_BY_CHANNEL[boot_channel],
        status=status,
        policy_ref=policy_ref,
        platform_entrypoints=platform_entrypoints,
        artifact_refs=artifacts,
        signing=signing,
        boot_capabilities=boot_capabilities,
        proof_reports=[str(item) for item in proof_reports],
        offline_fallback=offline_fallback,
        verification_gates=[],
        execute=False,
    )
    return ControlPlaneBootPlan(
        boot_release_set_id=plan.boot_release_set_id,
        base_release_set_ref=plan.base_release_set_ref,
        boot_mode=plan.boot_mode,
        boot_channel=plan.boot_channel,
        action=plan.action,
        status=plan.status,
        policy_ref=plan.policy_ref,
        platform_entrypoints=plan.platform_entrypoints,
        artifact_refs=plan.artifact_refs,
        signing=plan.signing,
        boot_capabilities=plan.boot_capabilities,
        proof_reports=plan.proof_reports,
        offline_fallback=plan.offline_fallback,
        verification_gates=_verification_gates(plan),
        execute=False,
    )
