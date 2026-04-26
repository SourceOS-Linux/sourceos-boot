"""nlboot-compatible SourceOS boot adapter.

This module defines the executable boundary between the nlboot safe planner
shape and SourceOS BootReleaseSet v1. It deliberately avoids network, disk, and
kexec side effects. It maps nlboot manifest/token/plan-shaped dictionaries into
SourceOS control-plane payloads and evidence envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


BOOT_MODE_TO_CHANNEL = {
    "installer": "installer",
    "recovery": "recovery",
    "ephemeral": "live",
    "bootstrap": "live",
}

BOOT_MODE_TO_ACTION = {
    "installer": "install",
    "recovery": "repair",
    "ephemeral": "kexec",
    "bootstrap": "enroll",
}


@dataclass(frozen=True)
class DeviceClaim:
    """Minimal self-registration claim emitted by a boot environment."""

    device_id: str
    public_key_fingerprint: str
    platform: str
    nonce: str
    observed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, str]:
        return {
            "deviceId": self.device_id,
            "publicKeyFingerprint": self.public_key_fingerprint,
            "platform": self.platform,
            "nonce": self.nonce,
            "observedAt": self.observed_at,
        }


@dataclass(frozen=True)
class BootAuthorization:
    """Authorization returned by the control plane for one boot transaction."""

    correlation_id: str
    boot_release_set_ref: str
    token_id: str
    expires_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "correlationId": self.correlation_id,
            "bootReleaseSetRef": self.boot_release_set_ref,
            "tokenId": self.token_id,
            "expiresAt": self.expires_at,
        }


@dataclass(frozen=True)
class BootEvidence:
    """Evidence envelope emitted for announce/authorize/fetch/verify stages."""

    correlation_id: str
    device_id: str
    selected_channel: str
    boot_mode: str
    manifest_hash: str
    verification_result: str
    reports: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlationId": self.correlation_id,
            "deviceId": self.device_id,
            "selectedChannel": self.selected_channel,
            "bootMode": self.boot_mode,
            "manifestHash": self.manifest_hash,
            "verificationResult": self.verification_result,
            "reports": self.reports,
        }


@dataclass(frozen=True)
class NlbootManifestView:
    """Normalized subset of nlboot SignedBootManifest fields."""

    manifest_id: str
    boot_release_set_id: str
    base_release_set_ref: str
    boot_mode: str
    artifacts: dict[str, str]
    signature_ref: str
    signer_ref: str
    signature_algorithm: str
    crypto_profile: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NlbootManifestView":
        return cls(
            manifest_id=_required_str(data, "manifest_id"),
            boot_release_set_id=_required_str(data, "boot_release_set_id"),
            base_release_set_ref=_required_str(data, "base_release_set_ref"),
            boot_mode=_required_str(data, "boot_mode"),
            artifacts=_required_dict_of_str(data, "artifacts"),
            signature_ref=_required_str(data, "signature_ref"),
            signer_ref=_required_str(data, "signer_ref"),
            signature_algorithm=_required_str(data, "signature_algorithm"),
            crypto_profile=_required_str(data, "crypto_profile"),
        )


@dataclass(frozen=True)
class NlbootTokenView:
    """Normalized subset of nlboot EnrollmentToken fields."""

    token_id: str
    purpose: str
    expires_at: str
    release_set_ref: str | None
    boot_release_set_ref: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NlbootTokenView":
        return cls(
            token_id=_required_str(data, "token_id"),
            purpose=_required_str(data, "purpose"),
            expires_at=_required_str(data, "expires_at"),
            release_set_ref=_optional_str(data, "release_set_ref"),
            boot_release_set_ref=_optional_str(data, "boot_release_set_ref"),
        )


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _required_dict_of_str(data: dict[str, Any], key: str) -> dict[str, str]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return {str(k): _required_str(value, str(k)) for k in value}


class SourceOSBootAdapter:
    """Pure adapter for nlboot-like control-plane handshakes.

    The runtime flow this class models is:

    announce -> authorize -> fetch manifest -> verify -> emit evidence
    """

    def build_announce_payload(self, claim: DeviceClaim) -> dict[str, Any]:
        return {"kind": "SourceOSBootAnnounce", "apiVersion": "sourceos.dev/v1", "claim": claim.to_dict()}

    def authorization_from_nlboot_token(self, token_doc: dict[str, Any], *, correlation_id: str) -> BootAuthorization:
        token = NlbootTokenView.from_dict(token_doc)
        if token.boot_release_set_ref is None:
            raise ValueError("nlboot token must include boot_release_set_ref")
        return BootAuthorization(
            correlation_id=correlation_id,
            boot_release_set_ref=token.boot_release_set_ref,
            token_id=token.token_id,
            expires_at=token.expires_at,
        )

    def build_fetch_request(self, authorization: BootAuthorization) -> dict[str, Any]:
        return {
            "kind": "SourceOSBootFetchRequest",
            "apiVersion": "sourceos.dev/v1",
            "authorization": authorization.to_dict(),
        }

    def boot_release_set_patch_from_nlboot_manifest(self, manifest_doc: dict[str, Any]) -> dict[str, Any]:
        manifest = NlbootManifestView.from_dict(manifest_doc)
        selected_channel = BOOT_MODE_TO_CHANNEL.get(manifest.boot_mode)
        if selected_channel is None:
            raise ValueError(f"unsupported nlboot boot_mode={manifest.boot_mode!r}")
        return {
            "releaseSetRef": manifest.base_release_set_ref,
            "channels": [selected_channel],
            "artifacts": [
                {"name": "kernel", "role": "kernel", "uri": manifest.artifacts["kernel_ref"], "sha256": _unknown_sha256()},
                {"name": "initrd", "role": "initrd", "uri": manifest.artifacts["initrd_ref"], "sha256": _unknown_sha256()},
                {"name": "rootfs", "role": "rootfs", "uri": manifest.artifacts["rootfs_ref"], "sha256": _unknown_sha256()},
            ],
            "signature": {
                "type": "x509" if manifest.signature_algorithm == "rsa-pss-sha256" else "other",
                "bundleRef": manifest.signature_ref,
                "digest": "sha256:" + _unknown_sha256(),
            },
            "provenance": {
                "builderId": manifest.signer_ref,
                "sourceRefs": [manifest.manifest_id],
                "attestations": ["slsa", "in-toto"],
            },
            "policy": {
                "allowedActions": ["announce", "enroll", "fetch", "verify", BOOT_MODE_TO_ACTION[manifest.boot_mode], "attest"]
            },
        }

    def build_evidence(
        self,
        *,
        claim: DeviceClaim,
        authorization: BootAuthorization,
        selected_channel: str,
        boot_mode: str,
        manifest_hash: str,
        verification_result: str,
    ) -> BootEvidence:
        reports = [
            "device-claim",
            "manifest-hash",
            "verification-result",
            "selected-channel",
            "boot-mode",
        ]
        return BootEvidence(
            correlation_id=authorization.correlation_id,
            device_id=claim.device_id,
            selected_channel=selected_channel,
            boot_mode=boot_mode,
            manifest_hash=manifest_hash,
            verification_result=verification_result,
            reports=reports,
        )

    def build_evidence_from_nlboot_manifest(
        self,
        *,
        claim: DeviceClaim,
        authorization: BootAuthorization,
        manifest_doc: dict[str, Any],
        manifest_hash: str,
        verification_result: str,
    ) -> BootEvidence:
        manifest = NlbootManifestView.from_dict(manifest_doc)
        channel = BOOT_MODE_TO_CHANNEL.get(manifest.boot_mode)
        if channel is None:
            raise ValueError(f"unsupported nlboot boot_mode={manifest.boot_mode!r}")
        return self.build_evidence(
            claim=claim,
            authorization=authorization,
            selected_channel=channel,
            boot_mode=manifest.boot_mode,
            manifest_hash=manifest_hash,
            verification_result=verification_result,
        )


def _unknown_sha256() -> str:
    return "0" * 64
