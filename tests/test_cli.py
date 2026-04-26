import json
from pathlib import Path

from sourceos_boot.cli import main


def test_adapt_nlboot_cli_emits_handoff_objects(capsys) -> None:
    root = Path(__file__).resolve().parents[1]
    rc = main([
        "adapt-nlboot",
        "--manifest",
        str(root / "examples" / "nlboot" / "manifest.json"),
        "--token",
        str(root / "examples" / "nlboot" / "token.json"),
        "--device-id",
        "device-1",
        "--public-key-fingerprint",
        "sha256:demo",
        "--platform",
        "apple-silicon",
        "--nonce",
        "nonce-1",
        "--correlation-id",
        "corr-1",
    ])
    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["kind"] == "NlbootAdapterOutput"
    assert output["authorization"]["bootReleaseSetRef"] == "boot/demo"
    assert output["bootReleaseSetPatch"]["channels"] == ["recovery"]
    assert output["evidence"]["deviceId"] == "device-1"
