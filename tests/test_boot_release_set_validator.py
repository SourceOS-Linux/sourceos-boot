from pathlib import Path

from sourceos_boot.validate_boot_release_set import main


def test_example_boot_release_set_validates() -> None:
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / "boot-release-set.example.json"
    assert main([str(example)]) == 0
