from pathlib import Path

from sourceos_boot.validate_trust_chain_boot_verification import main


ROOT = Path(__file__).resolve().parents[1]
VALID_FIXTURE = ROOT / "examples" / "trust-chain-boot-verification.valid.json"
BLOCKED_FIXTURE = ROOT / "examples" / "trust-chain-boot-verification.blocked.json"


def test_trust_chain_boot_verification_examples_validate() -> None:
    assert main([str(VALID_FIXTURE), str(BLOCKED_FIXTURE)]) == 0
