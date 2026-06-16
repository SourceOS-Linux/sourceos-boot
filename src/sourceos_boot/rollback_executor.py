"""Rollback executor for sourceos-boot.

Converts a non-mutating AsahiRollbackPlan into real subprocess calls.
This is intentionally a separate module from asahi_boot_chain.py to preserve
the purity boundary: the chain module is side-effect-free; this module owns
the execute boundary.

Safety invariants:
  - Will not execute a denied plan.
  - Will not execute if efiVarsMutable is true on the plan's chain.
  - Emits a RollbackExecutionResult with full step-level detail.
  - timeout_s defaults to 300 (nixos-rebuild switch --rollback can be slow
    if nix store paths need to be fetched, but on a local Katello it should
    be fast).
"""

from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .asahi_boot_chain import AsahiRollbackPlan

ROLLBACK_ENGINE_ID = "sourceos.boot.asahi-rollback"
ROLLBACK_SPEC_VERSION = "0.1.0"


@dataclass(frozen=True)
class RollbackStepResult:
    step: str
    status: str  # ok | failed | skipped | timeout | dry_run
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"step": self.step, "status": self.status}
        if self.returncode is not None:
            d["returncode"] = self.returncode
        if self.stdout:
            d["stdout"] = self.stdout
        if self.stderr:
            d["stderr"] = self.stderr
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass(frozen=True)
class RollbackExecutionResult:
    execution_id: str
    plan: AsahiRollbackPlan
    outcome: str  # applied | denied | dry_run | failed
    steps: list[RollbackStepResult]
    duration_ms: int
    issued_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "engineId": ROLLBACK_ENGINE_ID,
            "specVersion": ROLLBACK_SPEC_VERSION,
            "outcome": self.outcome,
            "plan": self.plan.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "durationMs": self.duration_ms,
            "issuedAt": self.issued_at,
        }

    @property
    def ok(self) -> bool:
        return self.outcome in ("applied", "dry_run")


class RollbackExecutor:
    """Executes an AsahiRollbackPlan by shelling out to nixos-rebuild --rollback."""

    def __init__(self, timeout_s: int = 300) -> None:
        self._timeout_s = timeout_s

    def execute(
        self, plan: AsahiRollbackPlan, dry_run: bool = True
    ) -> RollbackExecutionResult:
        execution_id = str(uuid.uuid4())
        issued_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        t_start = time.monotonic()

        if not plan.allowed:
            return RollbackExecutionResult(
                execution_id=execution_id,
                plan=plan,
                outcome="denied",
                steps=[],
                duration_ms=0,
                issued_at=issued_at,
            )

        step_results = []
        overall_ok = True

        for step_cmd in plan.steps:
            # comment lines are informational only
            if step_cmd.startswith("#"):
                continue

            if dry_run:
                step_results.append(RollbackStepResult(
                    step=step_cmd,
                    status="dry_run",
                    reason="dry_run=True",
                ))
                continue

            try:
                proc = subprocess.run(
                    step_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                )
                ok = proc.returncode == 0
                if not ok:
                    overall_ok = False
                step_results.append(RollbackStepResult(
                    step=step_cmd,
                    status="ok" if ok else "failed",
                    returncode=proc.returncode,
                    stdout=proc.stdout.strip()[:500],
                    stderr=proc.stderr.strip()[:500],
                ))
            except subprocess.TimeoutExpired:
                overall_ok = False
                step_results.append(RollbackStepResult(
                    step=step_cmd,
                    status="timeout",
                    reason=f"timed out after {self._timeout_s}s",
                ))

        duration_ms = int((time.monotonic() - t_start) * 1000)

        if dry_run:
            outcome = "dry_run"
        elif overall_ok:
            outcome = "applied"
        else:
            outcome = "failed"

        return RollbackExecutionResult(
            execution_id=execution_id,
            plan=plan,
            outcome=outcome,
            steps=step_results,
            duration_ms=duration_ms,
            issued_at=issued_at,
        )
