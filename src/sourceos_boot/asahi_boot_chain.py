"""Asahi Linux boot chain model for sourceos-boot.

Models the m1n1 → U-Boot → systemd-boot chain specific to Apple Silicon
devices running Asahi Linux. Provides a non-mutating rollback plan that
describes what a future executor would need to do to return to the previous
NixOS generation.

Boundary invariant: no disk writes, no EFI var mutations, no kexec, no
subprocess calls that modify state. plan_rollback() is pure.

Key Asahi constraint: efiVarsMutable MUST be false. NixOS systemd-boot
integration must be configured with canTouchEfiVariables = false or boot
entries may conflict with macOS's EFI namespace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

BOOT_CHAIN_TYPE = "asahi-m1n1-uboot-systemd-boot"
ASAHI_BOOT_SCHEMA = "sourceos.asahi-boot-chain/v0.1"

# The NixOS generations directory on a standard NixOS install
NIX_PROFILES_SYSTEM = "/nix/var/nix/profiles"
CURRENT_SYSTEM_LINK = "/run/current-system"
SYSTEM_PROFILE = "system"


@dataclass(frozen=True)
class AsahiBootChainInfo:
    """Static description of the Asahi boot chain for provenance records."""

    chain_type: str
    m1n1_version: str | None
    uboot_version: str | None
    efi_vars_mutable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.chain_type,
            "m1n1Version": self.m1n1_version,
            "ubootVersion": self.uboot_version,
            "efiVarsMutable": self.efi_vars_mutable,
        }

    def validate(self) -> list[str]:
        """Return a list of invariant violations. Empty list = valid."""
        issues = []
        if self.efi_vars_mutable:
            issues.append(
                "efiVarsMutable must be false on Apple Silicon — "
                "set boot.loader.efi.canTouchEfiVariables = false in NixOS config"
            )
        if self.chain_type != BOOT_CHAIN_TYPE:
            issues.append(f"unexpected chain_type {self.chain_type!r}; expected {BOOT_CHAIN_TYPE!r}")
        return issues


@dataclass(frozen=True)
class NixOSGeneration:
    number: int
    store_path: str
    is_current: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "store_path": self.store_path,
            "is_current": self.is_current,
        }


@dataclass(frozen=True)
class AsahiRollbackPlan:
    """Non-mutating rollback plan for an Asahi-booted NixOS device."""

    schema: str
    chain: AsahiBootChainInfo
    current_generation: NixOSGeneration | None
    rollback_target: NixOSGeneration | None
    policy_gate: str
    policy_reason: str
    steps: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "chain": self.chain.to_dict(),
            "current_generation": self.current_generation.to_dict() if self.current_generation else None,
            "rollback_target": self.rollback_target.to_dict() if self.rollback_target else None,
            "policy_gate": self.policy_gate,
            "policy_reason": self.policy_reason,
            "steps": self.steps,
        }

    @property
    def allowed(self) -> bool:
        return self.policy_gate == "allowed"


class AsahiBootChain:
    """Models the Asahi Linux boot chain and provides rollback planning.

    Reads the NixOS profile symlink tree to detect current and previous
    generations. All reads are from /nix/var/nix/profiles and /run —
    no writes, no subprocess calls.
    """

    def __init__(
        self,
        chain_info: AsahiBootChainInfo | None = None,
        profiles_root: str = NIX_PROFILES_SYSTEM,
        current_link: str = CURRENT_SYSTEM_LINK,
    ) -> None:
        self._chain = chain_info or AsahiBootChainInfo(
            chain_type=BOOT_CHAIN_TYPE,
            m1n1_version=None,
            uboot_version=None,
            efi_vars_mutable=False,
        )
        self._profiles_root = profiles_root
        self._current_link = current_link

    def detect_generations(self) -> list[NixOSGeneration]:
        """Read NixOS system profile symlinks and return all known generations.

        Returns an empty list if the profiles directory doesn't exist (e.g.
        running on macOS for testing).
        """
        generations = []
        system_profile_dir = os.path.join(self._profiles_root, SYSTEM_PROFILE)

        if not os.path.isdir(system_profile_dir):
            return generations

        current_path = None
        if os.path.islink(self._current_link):
            try:
                current_path = os.path.realpath(self._current_link)
            except OSError:
                pass

        for entry in sorted(os.listdir(system_profile_dir)):
            # NixOS generation symlinks are named system-<N>-link
            if not entry.startswith(SYSTEM_PROFILE + "-") or not entry.endswith("-link"):
                continue
            try:
                num_str = entry[len(SYSTEM_PROFILE) + 1 : -len("-link")]
                gen_num = int(num_str)
            except ValueError:
                continue
            link_path = os.path.join(system_profile_dir, entry)
            try:
                store_path = os.path.realpath(link_path)
            except OSError:
                continue
            is_current = current_path is not None and store_path == current_path
            generations.append(NixOSGeneration(
                number=gen_num,
                store_path=store_path,
                is_current=is_current,
            ))

        return sorted(generations, key=lambda g: g.number)

    def plan_rollback(self) -> AsahiRollbackPlan:
        """Return a non-mutating rollback plan. No writes performed."""

        violations = self._chain.validate()
        if violations:
            return AsahiRollbackPlan(
                schema=ASAHI_BOOT_SCHEMA,
                chain=self._chain,
                current_generation=None,
                rollback_target=None,
                policy_gate="denied",
                policy_reason="; ".join(violations),
                steps=[],
            )

        generations = self.detect_generations()
        current = next((g for g in generations if g.is_current), None)

        if not generations:
            # Running outside of a NixOS device (e.g. CI on macOS) —
            # emit a plan that describes the intent without real paths
            return AsahiRollbackPlan(
                schema=ASAHI_BOOT_SCHEMA,
                chain=self._chain,
                current_generation=None,
                rollback_target=None,
                policy_gate="allowed",
                policy_reason="no NixOS generations detected — rollback command shown for reference",
                steps=["nixos-rebuild switch --rollback"],
            )

        prev_gens = [g for g in generations if not g.is_current]
        rollback_target = prev_gens[-1] if prev_gens else None

        if rollback_target is None:
            return AsahiRollbackPlan(
                schema=ASAHI_BOOT_SCHEMA,
                chain=self._chain,
                current_generation=current,
                rollback_target=None,
                policy_gate="denied",
                policy_reason="no previous generation available to roll back to",
                steps=[],
            )

        steps = [
            f"nixos-rebuild switch --rollback",
            f"# rolls back to generation {rollback_target.number}: {rollback_target.store_path}",
            f"# efiVarsMutable=false enforced — boot entry managed by systemd-boot, not EFI vars",
        ]

        return AsahiRollbackPlan(
            schema=ASAHI_BOOT_SCHEMA,
            chain=self._chain,
            current_generation=current,
            rollback_target=rollback_target,
            policy_gate="allowed",
            policy_reason=f"rollback from generation {current.number if current else '?'} "
                          f"to generation {rollback_target.number} via nixos-rebuild --rollback",
            steps=steps,
        )
