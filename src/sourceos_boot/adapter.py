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

    # ── nlboot → SourceOS handoff ────────────────────────────────────────────────
    # The CLI's adapt-nlboot path, the fixtures, the Makefile target and
    # docs/APPLE_SILICON_EVIDENCE_NORMALIZATION.md all described these three methods.
    # None of them existed in any commit, so `make validate` failed on an AttributeError.

    @staticmethod
    def _require(doc: dict[str, Any], key: str, what: str) -> Any:
        value = doc.get(key)
        if value in (None, ""):
            raise KeyError(f"nlboot {what} is missing required field {key!r}")
        return value

    def authorization_from_nlboot_token(
        self, token_doc: dict[str, Any], *, correlation_id: str
    ) -> BootAuthorization:
        """Lift an nlboot token into the BootAuthorization this adapter speaks.

        correlation_id is supplied by the caller rather than read from the token: it
        ties one boot transaction together across announce/authorize/fetch/verify, and
        a token reused across transactions must not silently merge them.
        """
        return BootAuthorization(
            correlation_id=correlation_id,
            boot_release_set_ref=str(self._require(token_doc, "boot_release_set_ref", "token")),
            token_id=str(self._require(token_doc, "token_id", "token")),
            expires_at=str(self._require(token_doc, "expires_at", "token")),
        )

    def boot_release_set_patch_from_nlboot_manifest(self, manifest_doc: dict[str, Any]) -> dict[str, Any]:
        """The BootReleaseSet spec fields an nlboot manifest can actually supply.

        `releaseSetRef` and `channels` are derivable and emitted as spec fields.

        `spec.artifacts` is NOT. The schema requires a 64-hex sha256 on every artifact,
        and an nlboot manifest carries only refs — so a spec.artifacts built from it
        would either be schema-invalid or carry a fabricated digest, and a fabricated
        digest on a boot artifact is the worst possible lie to tell. The refs are
        carried under their own key instead, marked for what they are: candidates that
        cannot become artifacts until something supplies their digests.
        """
        artifacts = manifest_doc.get("artifacts") or {}
        roles = {"kernel_ref": "kernel", "initrd_ref": "initrd", "rootfs_ref": "rootfs"}
        pending = [
            {"role": role, "uri": str(artifacts[field])}
            for field, role in roles.items()
            if artifacts.get(field)
        ]
        return {
            "releaseSetRef": str(self._require(manifest_doc, "base_release_set_ref", "manifest")),
            "channels": [str(self._require(manifest_doc, "boot_mode", "manifest"))],
            # Deliberately not `artifacts`: these lack the sha256 the schema requires.
            "artifactRefsPendingDigest": pending,
        }

    def build_evidence_from_nlboot_manifest(
        self,
        *,
        claim: DeviceClaim,
        authorization: BootAuthorization,
        manifest_doc: dict[str, Any],
        manifest_hash: str,
        verification_result: str,
    ) -> BootEvidence:
        """Evidence for an nlboot-sourced boot, reusing the same envelope as every
        other path so one shape is emitted regardless of where the manifest came from."""
        boot_mode = str(self._require(manifest_doc, "boot_mode", "manifest"))
        return self.build_evidence(
            claim=claim,
            authorization=authorization,
            selected_channel=boot_mode,
            boot_mode=boot_mode,
            manifest_hash=manifest_hash,
            verification_result=verification_result,
        )
