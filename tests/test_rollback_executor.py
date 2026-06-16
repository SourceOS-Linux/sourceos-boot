"""Tests for RollbackExecutor."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sourceos_boot.asahi_boot_chain import (
    BOOT_CHAIN_TYPE,
    AsahiBootChain,
    AsahiBootChainInfo,
    AsahiRollbackPlan,
)
from sourceos_boot.rollback_executor import RollbackExecutor, ROLLBACK_ENGINE_ID


def _allowed_plan() -> AsahiRollbackPlan:
    chain = AsahiBootChain(profiles_root="/nonexistent")
    return chain.plan_rollback()


def _denied_plan() -> AsahiRollbackPlan:
    info = AsahiBootChainInfo(
        chain_type=BOOT_CHAIN_TYPE,
        m1n1_version=None,
        uboot_version=None,
        efi_vars_mutable=True,
    )
    chain = AsahiBootChain(chain_info=info, profiles_root="/nonexistent")
    return chain.plan_rollback()


# ── dry-run ────────────────────────────────────────────────────────────────────

def test_dry_run_returns_dry_run_outcome():
    plan = _allowed_plan()
    executor = RollbackExecutor()
    result = executor.execute(plan, dry_run=True)
    assert result.outcome == "dry_run"
    assert result.ok
    for step in result.steps:
        assert step.status == "dry_run"


def test_dry_run_skips_comment_lines():
    """Comment lines in plan.steps should not appear as step results."""
    plan = _allowed_plan()
    executor = RollbackExecutor()
    result = executor.execute(plan, dry_run=True)
    for step in result.steps:
        assert not step.step.startswith("#")


# ── denied plan ───────────────────────────────────────────────────────────────

def test_denied_plan_returns_denied_outcome():
    plan = _denied_plan()
    executor = RollbackExecutor()
    result = executor.execute(plan, dry_run=False)
    assert result.outcome == "denied"
    assert not result.ok
    assert result.steps == []


# ── execute ────────────────────────────────────────────────────────────────────

def test_execute_success():
    plan = _allowed_plan()
    executor = RollbackExecutor()
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "activating configuration..."
    mock_proc.stderr = ""
    with patch("sourceos_boot.rollback_executor.subprocess.run", return_value=mock_proc):
        result = executor.execute(plan, dry_run=False)
    assert result.outcome == "applied"
    assert result.ok
    assert all(s.status == "ok" for s in result.steps)


def test_execute_failure_propagates():
    plan = _allowed_plan()
    executor = RollbackExecutor()
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "error: rollback failed"
    with patch("sourceos_boot.rollback_executor.subprocess.run", return_value=mock_proc):
        result = executor.execute(plan, dry_run=False)
    assert result.outcome == "failed"
    assert not result.ok
    assert any(s.status == "failed" for s in result.steps)


# ── to_dict shape ──────────────────────────────────────────────────────────────

def test_to_dict_contains_required_fields():
    plan = _allowed_plan()
    executor = RollbackExecutor()
    result = executor.execute(plan, dry_run=True)
    d = result.to_dict()
    assert d["engineId"] == ROLLBACK_ENGINE_ID
    assert "executionId" in d
    assert "outcome" in d
    assert "issuedAt" in d
    assert "durationMs" in d
    assert isinstance(d["steps"], list)
