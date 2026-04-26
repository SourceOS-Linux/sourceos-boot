from sourceos_boot.adapter import BootAuthorization, DeviceClaim, SourceOSBootAdapter


def test_adapter_builds_announce_fetch_and_evidence() -> None:
    adapter = SourceOSBootAdapter()
    claim = DeviceClaim(
        device_id="demo-device",
        public_key_fingerprint="sha256:demo",
        platform="apple-silicon",
        nonce="nonce-1",
    )
    authorization = BootAuthorization(
        correlation_id="corr-1",
        boot_release_set_ref="boot-release-set/demo/0.1.0",
        token_id="token-1",
        expires_at="2026-04-26T01:00:00Z",
    )

    announce = adapter.build_announce_payload(claim)
    fetch = adapter.build_fetch_request(authorization)
    evidence = adapter.build_evidence(
        claim=claim,
        authorization=authorization,
        selected_channel="recovery",
        boot_mode="installer",
        manifest_hash="sha256:abc",
        verification_result="pass",
    )

    assert announce["kind"] == "SourceOSBootAnnounce"
    assert announce["claim"]["deviceId"] == "demo-device"
    assert fetch["authorization"]["correlationId"] == "corr-1"
    assert evidence.to_dict()["reports"] == [
        "device-claim",
        "manifest-hash",
        "verification-result",
        "selected-channel",
        "boot-mode",
    ]


def test_adapter_maps_nlboot_manifest_and_token() -> None:
    adapter = SourceOSBootAdapter()
    token_doc = {
        "token_id": "token-1",
        "purpose": "recovery",
        "expires_at": "2026-04-26T01:00:00Z",
        "release_set_ref": "release/demo",
        "boot_release_set_ref": "boot/demo",
    }
    manifest_doc = {
        "manifest_id": "manifest-1",
        "boot_release_set_id": "boot/demo",
        "base_release_set_ref": "release/demo",
        "boot_mode": "recovery",
        "artifacts": {
            "kernel_ref": "https://example.invalid/kernel",
            "initrd_ref": "https://example.invalid/initrd",
            "rootfs_ref": "https://example.invalid/rootfs",
        },
        "signature_ref": "urn:srcos:signature:demo",
        "signer_ref": "trusted-key-1",
        "signature_algorithm": "rsa-pss-sha256",
        "crypto_profile": "fips-140-3-compatible",
    }

    authorization = adapter.authorization_from_nlboot_token(token_doc, correlation_id="corr-2")
    patch = adapter.boot_release_set_patch_from_nlboot_manifest(manifest_doc)
    evidence = adapter.build_evidence_from_nlboot_manifest(
        claim=DeviceClaim("device-1", "sha256:demo", "apple-silicon", "nonce"),
        authorization=authorization,
        manifest_doc=manifest_doc,
        manifest_hash="sha256:manifest",
        verification_result="pass",
    )

    assert authorization.boot_release_set_ref == "boot/demo"
    assert patch["releaseSetRef"] == "release/demo"
    assert patch["channels"] == ["recovery"]
    assert patch["artifacts"][0]["role"] == "kernel"
    assert patch["signature"]["bundleRef"] == "urn:srcos:signature:demo"
    assert patch["provenance"]["builderId"] == "trusted-key-1"
    assert "repair" in patch["policy"]["allowedActions"]
    assert evidence.selected_channel == "recovery"
    assert evidence.boot_mode == "recovery"
