"""The coverage whose absence let auto-rollback die silently.

`sourceos-boot rollback execute --execute` is invoked by
source-os/modules/nixos/sourceos-syncd/default.nix under `rollbackOnFailure`. The
subcommand was dropped by #48 while `rollback_executor.py` — the library it fronts —
stayed, so every gate stayed green: the package's pythonImportsCheck imports the MODULE
(which still existed) and `doCheck = false` meant no test ever ran the CLI. Production
survived only on a flake.lock pin to bc6dd8c, the commit before the removal.

These tests exercise the CLI SURFACE, not the module, because that is the only thing that
could have caught it. `test_rollback_execute_is_a_valid_subcommand` is the specific test
that would have failed on #48.
"""
from __future__ import annotations

import argparse
import pytest

from sourceos_boot.cli import build_parser


def _parse(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def test_rollback_execute_is_a_valid_subcommand() -> None:
    """The exact invocation the NixOS module makes must parse."""
    args = _parse(["rollback", "execute", "--execute"])
    assert args.command == "rollback"
    assert args.rollback_command == "execute"
    assert args.execute is True
    assert callable(args.func)


def test_rollback_execute_defaults_to_dry_run() -> None:
    """Without --execute nothing may mutate: the flag is the safety, not the default."""
    args = _parse(["rollback", "execute"])
    assert args.execute is False


def test_rollback_plan_is_a_valid_subcommand() -> None:
    args = _parse(["rollback", "plan"])
    assert args.rollback_command == "plan"
    assert callable(args.func)


def test_rollback_requires_a_subcommand() -> None:
    """A bare `rollback` must fail loudly rather than silently doing nothing."""
    with pytest.raises(SystemExit) as exc:
        _parse(["rollback"])
    assert exc.value.code != 0


def test_the_module_invocation_survives_a_timeout_override() -> None:
    args = _parse(["rollback", "execute", "--execute", "--timeout", "600"])
    assert args.timeout == 600


def test_every_subcommand_the_nixos_module_invokes_exists() -> None:
    """Guards the CLASS of bug, not just this instance.

    Any binary invocation baked into a NixOS module must remain parseable here. When a new
    call-site is added to source-os, add it to this list — a module that shells out to a
    subcommand this parser rejects is a silent no-op in production, which is precisely how
    auto-rollback was disarmed.
    """
    invoked_by_nixos_modules = [
        # source-os/modules/nixos/sourceos-syncd/default.nix — healthCheck.rollbackOnFailure
        ["rollback", "execute", "--execute"],
    ]
    for argv in invoked_by_nixos_modules:
        args = _parse(argv)
        assert callable(args.func), f"{argv} parsed but is wired to no handler"
