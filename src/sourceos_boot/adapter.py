"""nlboot-compatible SourceOS boot adapter skeleton.

This module defines the first executable boundary between the original nlboot
shape and SourceOS BootReleaseSet v1. It deliberately does not perform network
or kexec actions yet; it normalizes request/response objects and produces an
evidence record that the boot client and Prophet Platform can agree on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


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


class SourceOSBootAdapter:
    """Pure adapter for the nlboot-like control-plane handshake.

    The runtime flow this class models is:

    announce -> authorize -> fetch manifest -> verify -> emit evidence
    """

    def build_announce_payload(self, claim: DeviceClaim) -> dict[str, Any]:
        return {"kind": "SourceOSBootAnnounce", "apiVersion": "sourceos.dev/v1", "claim": claim.to_dict()}

    def build_fetch_request(self, authorization: BootAuthorization) -> dict[str, Any]:
        return {
            "kind": "SourceOSBootFetchRequest",
            "apiVersion": "sourceos.dev/v1",
            "authorization": authorization.to_dict(),
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
