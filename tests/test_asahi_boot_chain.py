"""Tests for AsahiBootChain rollback planning."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sourceos_boot.asahi_boot_chain import (
    ASAHI_BOOT_SCHEMA,
    BOOT_CHAIN_TYPE,
    AsahiBootChain,
    AsahiBootChainInfo,
    NixOSGeneration,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = ROOT / "schemas" / "boot-release-set.schema.json"


# ── AsahiBootChainInfo.validate ────────────────────────────────────────────

def test_valid_chain_no_violations():
    chain = AsahiBootChainInfo(
        chain_type=BOOT_CHAIN_TYPE,
        m1n1_version="1.4.x",
        uboot_version="2024.01",
        efi_vars_mutable=False,
    )
    assert chain.validate() == []


def test_efi_vars_mutable_is_violation():
    chain = AsahiBootChainInfo(
        chain_type=BOOT_CHAIN_TYPE,
        m1n1_version=None,
        uboot_version=None,
        efi_vars_mutable=True,
    )
    violations = chain.validate()
    assert len(violations) == 1
    assert "efiVarsMutable" in violations[0]


def test_wrong_chain_type_is_violation():
    chain = AsahiBootChainInfo(
        chain_type="uefi-grub",
        m1n1_version=None,
        uboot_version=None,
        efi_vars_mutable=False,
    )
    violations = chain.validate()
    assert any("chain_type" in v for v in violations)


def test_to_dict_contains_required_keys():
    chain = AsahiBootChainInfo(
        chain_type=BOOT_CHAIN_TYPE,
        m1n1_version="1.4.x",
        uboot_version="2024.01",
        efi_vars_mutable=False,
    )
    d = chain.to_dict()
    assert d["type"] == BOOT_CHAIN_TYPE
    assert d["efiVarsMutable"] is False
    assert d["m1n1Version"] == "1.4.x"


# ── AsahiBootChain.plan_rollback — no device ──────────────────────────────

def test_plan_rollback_no_profiles_dir():
    """Outside a NixOS device: returns allowed plan with reference command."""
    chain = AsahiBootChain(profiles_root="/nonexistent/profiles")
    plan = chain.plan_rollback()
    assert plan.allowed
    assert plan.policy_gate == "allowed"
    assert any("nixos-rebuild" in s for s in plan.steps)


def test_plan_rollback_denied_when_efi_vars_mutable():
    info = AsahiBootChainInfo(
        chain_type=BOOT_CHAIN_TYPE,
        m1n1_version=None,
        uboot_version=None,
        efi_vars_mutable=True,
    )
    chain = AsahiBootChain(chain_info=info, profiles_root="/nonexistent")
    plan = chain.plan_rollback()
    assert plan.policy_gate == "denied"
    assert not plan.allowed


# ── AsahiBootChain.plan_rollback — simulated NixOS profiles ───────────────

def _make_profiles(tmpdir: str, num_generations: int, current_gen: int) -> tuple[str, str]:
    """Create a fake NixOS profiles dir with symlinks."""
    profiles = os.path.join(tmpdir, "nix", "var", "nix", "profiles", "system")
    os.makedirs(profiles)
    current_link = os.path.join(tmpdir, "current-system")

    for i in range(1, num_generations + 1):
        store_path = os.path.join(tmpdir, f"nix", "store", f"gen-{i}-drv")
        os.makedirs(store_path, exist_ok=True)
        link = os.path.join(profiles, f"system-{i}-link")
        os.symlink(store_path, link)
        if i == current_gen:
            os.symlink(store_path, current_link)

    return profiles, current_link


def test_plan_rollback_single_generation_denied():
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles_root = os.path.join(tmpdir, "nix", "var", "nix", "profiles")
        profiles, current_link = _make_profiles(tmpdir, num_generations=1, current_gen=1)
        chain = AsahiBootChain(
            profiles_root=profiles_root,
            current_link=current_link,
        )
        plan = chain.plan_rollback()
        assert plan.policy_gate == "denied"
        assert "no previous generation" in plan.policy_reason


def test_plan_rollback_two_generations_allowed():
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles_root = os.path.join(tmpdir, "nix", "var", "nix", "profiles")
        profiles, current_link = _make_profiles(tmpdir, num_generations=2, current_gen=2)
        chain = AsahiBootChain(
            profiles_root=profiles_root,
            current_link=current_link,
        )
        plan = chain.plan_rollback()
        assert plan.allowed
        assert plan.rollback_target is not None
        assert plan.rollback_target.number == 1
        assert plan.current_generation is not None
        assert plan.current_generation.number == 2


def test_plan_rollback_targets_most_recent_previous():
    """With three generations, rolls back to gen 2, not gen 1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        profiles_root = os.path.join(tmpdir, "nix", "var", "nix", "profiles")
        profiles, current_link = _make_profiles(tmpdir, num_generations=3, current_gen=3)
        chain = AsahiBootChain(
            profiles_root=profiles_root,
            current_link=current_link,
        )
        plan = chain.plan_rollback()
        assert plan.allowed
        assert plan.rollback_target.number == 2


def test_plan_rollback_to_dict_schema():
    chain = AsahiBootChain(profiles_root="/nonexistent")
    plan = chain.plan_rollback()
    d = plan.to_dict()
    assert d["schema"] == ASAHI_BOOT_SCHEMA
    assert "chain" in d
    assert "policy_gate" in d
    assert "steps" in d


# ── BootReleaseSet schema: bootChain field ────────────────────────────────

@pytest.fixture(scope="module")
def brs_schema() -> dict:
    data = json.loads(SCHEMA_FILE.read_text())
    Draft202012Validator.check_schema(data)
    return data


def test_boot_chain_field_in_schema(brs_schema):
    spec_props = brs_schema["properties"]["spec"]["properties"]
    assert "bootChain" in spec_props


def test_boot_chain_type_enum(brs_schema):
    boot_chain = brs_schema["properties"]["spec"]["properties"]["bootChain"]
    chain_types = boot_chain["properties"]["type"]["enum"]
    assert "asahi-m1n1-uboot-systemd-boot" in chain_types
    assert "uefi-systemd-boot" in chain_types


def test_example_fixture_validates(brs_schema):
    example = json.loads(
        (ROOT / "examples" / "builder-aarch64-dev-release.example.json").read_text()
    )
    errors = list(Draft202012Validator(brs_schema).iter_errors(example))
    assert errors == [], [e.message for e in errors]
