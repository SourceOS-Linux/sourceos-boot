#!/usr/bin/env python3
"""Validate BootReleaseSet example documents.

This validator intentionally remains dependency-light for CI, while enforcing the
v1 world-class contract fields: provenance, trust, signature, anti-rollback, and
telemetry.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
SHA256_PREFIX_RE = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")
VERSION_RE = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+([-.+][A-Za-z0-9.-]+)?$")
PLATFORMS = {"apple-silicon", "uefi-x86_64", "uefi-aarch64", "generic-arm64"}
CHANNELS = {"live", "installer", "recovery", "rollback", "rescue"}
ARTIFACT_ROLES = {
    "kernel",
    "initrd",
    "rootfs",
    "manifest",
    "bootloader",
    "recovery-image",
    "installer-data",
    "signature",
    "attestation",
    "tuf-metadata",
    "other",
}
NETWORK = {"none", "enrollment-only", "restricted", "full"}
DISK_WRITE = {"forbidden", "installer-only", "recovery-only", "allowed"}
ACTIONS = {"announce", "enroll", "fetch", "verify", "kexec", "install", "rollback", "repair", "rekey", "attest"}
REPORTS = {
    "device-claim",
    "manifest-hash",
    "verification-result",
    "selected-channel",
    "boot-mode",
    "install-result",
    "rollback-result",
    "measurement",
    "attestation",
}
ATTESTATIONS = {"slsa", "in-toto"}
TRUST_MODELS = {"tuf", "uptane", "static-root"}
SIGNATURE_TYPES = {"sigstore", "cosign", "minisign", "x509", "other"}
METRICS = {"boot-duration", "verify-duration", "download-bytes", "action-result"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_enum_list(values: object, allowed: set[str], field: str) -> None:
    require(isinstance(values, list), f"{field} must be a list")
    require(values, f"{field} must not be empty")
    require(len(values) == len(set(values)), f"{field} must not contain duplicates")
    invalid = sorted(set(values) - allowed)
    require(not invalid, f"{field} contains invalid values: {invalid}")


def require_string(value: object, field: str) -> None:
    require(isinstance(value, str) and bool(value), f"{field} must be a non-empty string")


def require_uri(value: object, field: str) -> None:
    require_string(value, field)
    parsed = urlparse(value)
    require(parsed.scheme in {"https", "file", "oci"}, f"{field} must use https, file, or oci scheme")
    require(bool(parsed.netloc) or parsed.scheme in {"file", "oci"}, f"{field} must include a location")


def validate_document(doc: dict) -> None:
    require(doc.get("apiVersion") == "sourceos.dev/v1", "apiVersion must be sourceos.dev/v1")
    require(doc.get("kind") == "BootReleaseSet", "kind must be BootReleaseSet")

    metadata = doc.get("metadata")
    require(isinstance(metadata, dict), "metadata must be an object")
    require(NAME_RE.match(metadata.get("name", "")) is not None, "metadata.name is invalid")
    require(VERSION_RE.match(metadata.get("version", "")) is not None, "metadata.version is invalid")
    require_string(metadata.get("createdAt"), "metadata.createdAt")

    spec = doc.get("spec")
    require(isinstance(spec, dict), "spec must be an object")
    require_enum_list(spec.get("platforms"), PLATFORMS, "spec.platforms")
    require_enum_list(spec.get("channels"), CHANNELS, "spec.channels")

    artifacts = spec.get("artifacts")
    require(isinstance(artifacts, list) and artifacts, "spec.artifacts must be a non-empty list")
    for index, artifact in enumerate(artifacts):
        prefix = f"spec.artifacts[{index}]"
        require(isinstance(artifact, dict), f"{prefix} must be an object")
        require_string(artifact.get("name"), f"{prefix}.name")
        require(artifact.get("role") in ARTIFACT_ROLES, f"{prefix}.role is invalid")
        require_uri(artifact.get("uri"), f"{prefix}.uri")
        require(SHA256_RE.match(artifact.get("sha256", "")) is not None, f"{prefix}.sha256 must be a 64-character hex digest")
        if "sizeBytes" in artifact:
            require(isinstance(artifact["sizeBytes"], int) and artifact["sizeBytes"] >= 0, f"{prefix}.sizeBytes must be a non-negative integer")

    policy = spec.get("policy")
    require(isinstance(policy, dict), "spec.policy must be an object")
    require(policy.get("network") in NETWORK, "spec.policy.network is invalid")
    require(policy.get("diskWrite") in DISK_WRITE, "spec.policy.diskWrite is invalid")
    require(isinstance(policy.get("tokenRequired"), bool), "spec.policy.tokenRequired must be a boolean")
    require_enum_list(policy.get("allowedActions"), ACTIONS, "spec.policy.allowedActions")

    evidence = spec.get("evidence")
    require(isinstance(evidence, dict), "spec.evidence must be an object")
    require_string(evidence.get("correlationId"), "spec.evidence.correlationId")
    require_enum_list(evidence.get("requiredReports"), REPORTS, "spec.evidence.requiredReports")

    provenance = spec.get("provenance")
    require(isinstance(provenance, dict), "spec.provenance must be an object")
    require_string(provenance.get("builderId"), "spec.provenance.builderId")
    require(isinstance(provenance.get("sourceRefs"), list) and provenance["sourceRefs"], "spec.provenance.sourceRefs must be a non-empty list")
    for index, ref in enumerate(provenance["sourceRefs"]):
        require_string(ref, f"spec.provenance.sourceRefs[{index}]")
    require_enum_list(provenance.get("attestations"), ATTESTATIONS, "spec.provenance.attestations")

    trust = spec.get("trust")
    require(isinstance(trust, dict), "spec.trust must be an object")
    require(trust.get("model") in TRUST_MODELS, "spec.trust.model is invalid")
    require_string(trust.get("rootRef"), "spec.trust.rootRef")
    require_string(trust.get("metadataRef"), "spec.trust.metadataRef")
    if "threshold" in trust:
        require(isinstance(trust["threshold"], int) and trust["threshold"] >= 1, "spec.trust.threshold must be >= 1")

    signature = spec.get("signature")
    require(isinstance(signature, dict), "spec.signature must be an object")
    require(signature.get("type") in SIGNATURE_TYPES, "spec.signature.type is invalid")
    require(SHA256_PREFIX_RE.match(signature.get("digest", "")) is not None, "spec.signature.digest must be sha256:<64 hex chars>")

    anti_rollback = spec.get("antiRollback")
    require(isinstance(anti_rollback, dict), "spec.antiRollback must be an object")
    require_string(anti_rollback.get("minimumVersion"), "spec.antiRollback.minimumVersion")
    require(isinstance(anti_rollback.get("allowOfflineFallback"), bool), "spec.antiRollback.allowOfflineFallback must be a boolean")

    telemetry = spec.get("telemetry")
    require(isinstance(telemetry, dict), "spec.telemetry must be an object")
    require(isinstance(telemetry.get("traceRequired"), bool), "spec.telemetry.traceRequired must be a boolean")
    require_enum_list(telemetry.get("metricSet"), METRICS, "spec.telemetry.metricSet")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate BootReleaseSet JSON files")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    failed = False
    for path in args.paths:
        try:
            with path.open("r", encoding="utf-8") as handle:
                doc = json.load(handle)
            validate_document(doc)
            print(f"PASS {path}")
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"FAIL {path}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
