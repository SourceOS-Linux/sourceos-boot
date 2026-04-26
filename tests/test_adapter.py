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
