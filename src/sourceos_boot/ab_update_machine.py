#!/usr/bin/env python3
"""A/B dual-slot update state machine — the reference implementation of the
sourceos-spec A/B fallback update contract v0.1.

The contract lives in ``sourceos-spec`` (``schemas/UpdateSlot.json``,
``UpdateTransaction.json``, ``UpdateHealthProbe.json``, normative notes in
``specs/ab-fallback-update-contract.md``). Copies of those three schemas are
vendored under ``schemas/ab-update/`` so this repo can prove its own output
conforms without a cross-repo import. This module is the ENGINE that enforces
the transitions between the contract's states: the explicit allowed-transition
table, fail-closed admission of illegal events, the attempt budget, and the
rollback paths.

THE INVARIANT
-------------
The currently-good slot is never overwritten by the update being applied.

It is checked after EVERY transition by ``_assert_invariants`` and raises
``InvariantViolation`` — not a returned error code, and not a test-only
assertion. A library whose central guarantee is enforced only by its tests
guarantees nothing to a caller who takes a path the tests do not cover, and this
is the one property whose violation cannot be recovered from: once the good
payload is gone, there is nothing to fall back to.

WHY THE ATTEMPT BUDGET IS SPENT BY THE BOOTLOADER
-------------------------------------------------
``tries_remaining`` is decremented in ``boot_attempt()``, which models the
bootloader handing over control — BEFORE the payload runs, not after it reports.
A counter decremented after a successful boot never terminates for a payload
that cannot reach userspace, and that payload is precisely the one that most
needs automatic fallback. ``refused`` is terminal for the same reason: it is the
state that ends the boot loop, and every event offered to a terminal machine is
refused rather than silently retried.

Zero-dependency, pure stdlib, side-effect free (no I/O, no subprocess). Driving
real hardware is the caller's business; deciding what is legal is this module's.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

SPEC_VERSION = "0.1.0"

# GPT priority values used while a candidate is on trial. The candidate takes the
# ceiling and the active slot sits one below it: strictly ordered, both non-zero,
# so the selector has exactly one answer and the fallback is still selectable.
ARMED_CANDIDATE_PRIORITY = 15
ARMED_ACTIVE_PRIORITY = 14

# ── phases ─────────────────────────────────────────────────────────────────────
# The transaction's phase. UpdateTransaction.outcome is the terminal projection
# of this set (see PHASE_TO_OUTCOME).
PHASES = {
    "idle",
    "writing",
    "candidate_ready",
    "armed",
    "trying",
    "promoted",
    "rolled_back",
    "refused",
}

TERMINAL_PHASES = {"promoted", "rolled_back", "refused"}

PHASE_TO_OUTCOME = {
    "idle": "pending",
    "writing": "pending",
    "candidate_ready": "pending",
    "armed": "pending",
    "trying": "pending",
    "promoted": "promoted",
    "rolled_back": "rolled-back",
    "refused": "refused",
}

# ── slot states (must stay a subset of UpdateSlot.state) ───────────────────────
SLOT_STATES = {"empty", "writing", "written", "trying", "good", "unbootable"}

# Closed rollback-reason set, mirroring UpdateTransaction.rollbackReason.
ROLLBACK_REASONS = {
    "probe-failed",
    "watchdog-expired",
    "attempts-exhausted",
    "write-failed",
    "digest-mismatch",
    "operator-abort",
}


class InvariantViolation(RuntimeError):
    """The good slot was overwritten, or the machine reached a state the contract
    forbids. Raised, never returned: a caller that can ignore this has no
    fallback and does not know it."""


@dataclass(frozen=True)
class SlotState:
    """One slot's on-device state — the UpdateSlot attribute triple plus payload.

    Mirrors the contract field-for-field so a SlotState round-trips through an
    UpdateSlot document without a translation layer that could drift.
    """

    slot: str  # "A" | "B"
    role: str  # "active" | "candidate"
    state: str
    boot_priority: int
    tries_remaining: int
    successful: bool
    currently_running: bool
    payload_digest: str | None = None
    payload_ref: str | None = None
    version: str | None = None
    last_probe_verdict: str | None = None

    def __post_init__(self) -> None:
        if self.slot not in {"A", "B"}:
            raise ValueError(f"slot must be 'A' or 'B', got {self.slot!r}")
        if self.role not in {"active", "candidate"}:
            raise ValueError(f"role must be 'active' or 'candidate', got {self.role!r}")
        if self.state not in SLOT_STATES:
            raise ValueError(f"unknown slot state {self.state!r}")


@dataclass(frozen=True)
class AttemptRecord:
    """One trial boot into the candidate slot."""

    attempt_number: int
    started_at: str
    result: str  # pass | probe-failed | watchdog-expired | write-failed | aborted
    fell_back_to: str | None
    tries_remaining_after: int
    detail: str | None = None

    def to_dict(self) -> dict:
        out: dict = {
            "attemptNumber": self.attempt_number,
            "startedAt": self.started_at,
            "result": self.result,
            "fellBackTo": self.fell_back_to,
            "triesRemainingAfter": self.tries_remaining_after,
        }
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class TransitionRule:
    """One legal edge of the machine.

    ``guard`` names a condition resolved at runtime when a single event has more
    than one legal target — a write that may or may not match its digest, a
    failed attempt that may or may not have budget left. Encoding the branch as a
    named guard rather than as an ``if`` inside the handler keeps the whole legal
    edge set readable in one table, which is the only way a reader can confirm
    that nothing reaches ``promoted`` except through a passing probe.
    """

    event: str
    from_phase: str
    to_phase: str
    guard: str | None = None


# ── the explicit allowed-transition table ──────────────────────────────────────
# Anything not in this table is illegal (fail-closed). Note what is absent:
# there is no edge into `promoted` from anything but a passing probe, and no
# edge out of any terminal phase at all.
TRANSITION_TABLE: tuple[TransitionRule, ...] = (
    TransitionRule("begin", "idle", "writing"),
    # A write either verifies or it does not. Both outcomes leave the active slot
    # untouched; only the candidate is ever the write target.
    TransitionRule("write_complete", "writing", "candidate_ready", guard="digest_matches"),
    TransitionRule("write_complete", "writing", "rolled_back", guard="digest_mismatch"),
    TransitionRule("write_failed", "writing", "rolled_back"),
    TransitionRule("arm", "candidate_ready", "armed"),
    TransitionRule("boot_attempt", "armed", "trying"),
    # The only path to promotion.
    TransitionRule("probe_pass", "trying", "promoted"),
    # A failure either has budget left to retry, or it does not and the candidate
    # is marked unbootable. There is no third option, which is what bounds it.
    TransitionRule("probe_fail", "trying", "armed", guard="tries_remain"),
    TransitionRule("probe_fail", "trying", "refused", guard="tries_exhausted"),
    TransitionRule("watchdog_expired", "trying", "armed", guard="tries_remain"),
    TransitionRule("watchdog_expired", "trying", "refused", guard="tries_exhausted"),
    # An operator may abandon a transaction at any pre-terminal phase after the
    # write has started. The target returns to the slot it started on.
    TransitionRule("operator_abort", "writing", "rolled_back"),
    TransitionRule("operator_abort", "candidate_ready", "rolled_back"),
    TransitionRule("operator_abort", "armed", "rolled_back"),
    TransitionRule("operator_abort", "trying", "rolled_back"),
)

# index for O(1) lookup: (from_phase, event) -> [rules]
_INDEX: dict[tuple[str, str], list[TransitionRule]] = {}
for _rule in TRANSITION_TABLE:
    _INDEX.setdefault((_rule.from_phase, _rule.event), []).append(_rule)


@dataclass(frozen=True)
class TransitionResult:
    """The outcome of offering one event to the machine.

    Refusals are returned, not raised: an illegal event is an ordinary thing for
    a caller to attempt (a retry after refusal, a duplicate probe report) and the
    machine's job is to decline it and say why. InvariantViolation is reserved
    for the one condition a caller must never be allowed to continue past.
    """

    allowed: bool
    event: str
    from_phase: str
    to_phase: str
    reason: str
    fell_back_to: str | None = None


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AbUpdateMachine:
    """Drives one UpdateTransaction over a two-slot target.

    Construct with the target's current slot pair. ``begin()`` opens the
    transaction; the write target is derived, never chosen by the caller, which
    removes the only way a caller could aim an update at the slot it falls back
    to.
    """

    target_ref: str
    transaction_id: str
    slots: dict[str, SlotState]
    health_probe_ref: str
    health_probe_digest: str
    max_attempts: int = 2
    clock: Callable[[], str] = _utc_now

    _phase: str = field(default="idle", init=False)
    _from_slot: str = field(default="", init=False)
    _to_slot: str = field(default="", init=False)
    _preserved_digest: str = field(default="", init=False)
    _candidate_digest: str = field(default="", init=False)
    _attempts: list[AttemptRecord] = field(default_factory=list, init=False)
    _rollback_reason: str | None = field(default=None, init=False)
    _opened_at: str | None = field(default=None, init=False)
    _settled_at: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if set(self.slots) != {"A", "B"}:
            raise ValueError("a target has exactly two slots, labelled A and B")
        for label, slot in self.slots.items():
            if slot.slot != label:
                raise ValueError(f"slot keyed {label!r} carries label {slot.slot!r}")
        actives = [s for s in self.slots.values() if s.role == "active"]
        if len(actives) != 1:
            raise ValueError("exactly one slot must hold role 'active'")
        if not actives[0].successful:
            raise InvariantViolation(
                "the active slot is not marked successful — there is nothing "
                "known-good to fall back to, so no update may be started"
            )
        if self.max_attempts < 1 or self.max_attempts > 7:
            raise ValueError("max_attempts must be within the GPT tries width (1..7)")

    # ── read-only views ────────────────────────────────────────────────────────
    @property
    def phase(self) -> str:
        return self._phase

    @property
    def attempts(self) -> tuple[AttemptRecord, ...]:
        return tuple(self._attempts)

    @property
    def rollback_reason(self) -> str | None:
        return self._rollback_reason

    @property
    def active(self) -> SlotState:
        """The known-good slot — the one the target falls back to.

        Distinct from `running`: during a trial boot the candidate is executing
        while this slot is still the fallback.
        """
        return next(s for s in self.slots.values() if s.role == "active")

    @property
    def candidate(self) -> SlotState:
        return next(s for s in self.slots.values() if s.role == "candidate")

    @property
    def running(self) -> SlotState:
        return next(s for s in self.slots.values() if s.currently_running)

    # ── invariants ─────────────────────────────────────────────────────────────
    def _assert_invariants(self) -> None:
        """Checked after every transition. Raises rather than returns."""
        if self._from_slot:
            preserved = self.slots[self._from_slot]
            if preserved.payload_digest != self._preserved_digest:
                raise InvariantViolation(
                    f"the good slot was overwritten: transaction opened with "
                    f"{self._preserved_digest} in slot {self._from_slot}, which now "
                    f"holds {preserved.payload_digest}"
                )
            if not preserved.successful:
                raise InvariantViolation(
                    f"slot {self._from_slot} lost its successful marking mid-transaction "
                    "— the fallback is no longer provably good"
                )
        if sum(1 for s in self.slots.values() if s.currently_running) != 1:
            raise InvariantViolation("exactly one slot may be currently running")
        if sum(1 for s in self.slots.values() if s.role == "active") != 1:
            raise InvariantViolation("exactly one slot may hold role 'active'")
        for slot in self.slots.values():
            if slot.state == "writing" and slot.role != "candidate":
                raise InvariantViolation(
                    f"slot {slot.slot} is being written while holding role "
                    f"{slot.role!r} — only the candidate is ever a write target"
                )
            if slot.state == "unbootable" and slot.boot_priority != 0:
                raise InvariantViolation(
                    f"slot {slot.slot} is unbootable but still selectable "
                    f"(bootPriority={slot.boot_priority})"
                )
        if self._phase in {"armed", "trying"}:
            candidate, active = self.candidate, self.active
            if candidate.boot_priority <= active.boot_priority:
                raise InvariantViolation(
                    f"boot priority tie or inversion while on trial: candidate "
                    f"{candidate.slot}={candidate.boot_priority}, active "
                    f"{active.slot}={active.boot_priority} — slot selection is "
                    "undefined, so which payload boots is not a recorded fact"
                )
            if active.boot_priority < 1:
                raise InvariantViolation(
                    f"the fallback slot {active.slot} was made unselectable "
                    "(bootPriority=0) while a candidate is on trial"
                )

    # ── guards ─────────────────────────────────────────────────────────────────
    def _guard(self, name: str, **kwargs) -> bool:
        if name == "digest_matches":
            return kwargs["observed_digest"] == self._candidate_digest
        if name == "digest_mismatch":
            return kwargs["observed_digest"] != self._candidate_digest
        if name == "tries_remain":
            return self.candidate.tries_remaining > 0
        if name == "tries_exhausted":
            return self.candidate.tries_remaining == 0
        raise InvariantViolation(f"unknown guard {name!r}")

    def _resolve(self, event: str, **kwargs) -> TransitionRule | None:
        candidates = _INDEX.get((self._phase, event), [])
        for rule in candidates:
            if rule.guard is None or self._guard(rule.guard, **kwargs):
                return rule
        return None

    def _refuse(self, event: str, reason: str) -> TransitionResult:
        return TransitionResult(
            allowed=False,
            event=event,
            from_phase=self._phase,
            to_phase=self._phase,
            reason=reason,
        )

    def _offer(self, event: str, **kwargs) -> TransitionRule | TransitionResult:
        """Fail-closed admission. Terminal phases refuse everything."""
        if self._phase in TERMINAL_PHASES:
            return self._refuse(
                event,
                f"transaction is terminal in phase {self._phase!r}; no further "
                "attempt will be made (this is what ends the boot loop)",
            )
        rule = self._resolve(event, **kwargs)
        if rule is None:
            return self._refuse(event, f"event {event!r} is illegal in phase {self._phase!r}")
        return rule

    def _set_slot(self, label: str, **changes) -> None:
        self.slots[label] = replace(self.slots[label], **changes)

    # ── events ─────────────────────────────────────────────────────────────────
    def begin(self, payload_digest: str, *, payload_ref: str | None = None,
              version: str | None = None) -> TransitionResult:
        """Open the transaction and start writing.

        The write target is DERIVED from the current roles, never supplied. There
        is deliberately no parameter by which a caller could name the active slot
        as the destination.
        """
        outcome = self._offer("begin")
        if isinstance(outcome, TransitionResult):
            return outcome

        self._from_slot = self.active.slot
        self._to_slot = self.candidate.slot
        self._preserved_digest = self.active.payload_digest or ""
        self._candidate_digest = payload_digest
        self._opened_at = self.clock()

        self._set_slot(
            self._to_slot,
            state="writing",
            successful=False,
            payload_digest=payload_digest,
            payload_ref=payload_ref,
            version=version,
            last_probe_verdict=None,
        )
        self._phase = outcome.to_phase
        self._assert_invariants()
        return TransitionResult(True, "begin", "idle", self._phase,
                                f"writing candidate payload to slot {self._to_slot}")

    def write_complete(self, observed_digest: str) -> TransitionResult:
        """Finish the write and verify what actually landed."""
        outcome = self._offer("write_complete", observed_digest=observed_digest)
        if isinstance(outcome, TransitionResult):
            return outcome
        from_phase = self._phase

        if outcome.to_phase == "rolled_back":
            self._mark_candidate_unbootable()
            self._settle("rolled_back", "digest-mismatch")
            return TransitionResult(
                True, "write_complete", from_phase, self._phase,
                f"digest mismatch: expected {self._candidate_digest}, wrote "
                f"{observed_digest}; candidate never armed, target stays on "
                f"slot {self._from_slot}",
                fell_back_to=self._from_slot,
            )

        self._set_slot(self._to_slot, state="written")
        self._phase = outcome.to_phase
        self._assert_invariants()
        return TransitionResult(True, "write_complete", from_phase, self._phase,
                                "candidate written and digest-verified")

    def write_failed(self, detail: str = "write failed") -> TransitionResult:
        outcome = self._offer("write_failed")
        if isinstance(outcome, TransitionResult):
            return outcome
        from_phase = self._phase
        self._mark_candidate_unbootable()
        self._settle("rolled_back", "write-failed")
        return TransitionResult(True, "write_failed", from_phase, self._phase,
                                f"{detail}; target stays on slot {self._from_slot}",
                                fell_back_to=self._from_slot)

    def arm(self) -> TransitionResult:
        """Give the candidate boot priority and its attempt budget.

        The candidate takes the top priority and the active slot is DEMOTED to
        just below it, rather than the candidate being raised toward the active.
        Raising has no headroom when the active slot is already at the ceiling:
        both slots end up equal, and a priority tie leaves slot selection
        undefined — the bootloader picks one of two payloads with nothing in the
        recorded state saying which. Demotion always has room and always leaves a
        strict order. The active slot keeps a non-zero priority and its
        ``successful`` marking throughout, so it remains a usable fallback; only
        its rank moves, never its payload.

        ``successful`` is set false on the candidate here and can only be set
        true by a passing probe. Arming raises priority; it never certifies.
        """
        outcome = self._offer("arm")
        if isinstance(outcome, TransitionResult):
            return outcome
        from_phase = self._phase
        self._set_slot(self._from_slot, boot_priority=ARMED_ACTIVE_PRIORITY)
        self._set_slot(
            self._to_slot,
            boot_priority=ARMED_CANDIDATE_PRIORITY,
            tries_remaining=self.max_attempts,
            successful=False,
        )
        self._phase = outcome.to_phase
        self._assert_invariants()
        return TransitionResult(True, "arm", from_phase, self._phase,
                                f"candidate armed with {self.max_attempts} attempt(s)")

    def boot_attempt(self) -> TransitionResult:
        """The bootloader hands control to the candidate.

        The attempt is spent HERE, before the payload runs. A payload that hangs
        before userspace still consumes its attempt, which is the only reason the
        budget terminates for the failure mode that most needs it to.
        """
        outcome = self._offer("boot_attempt")
        if isinstance(outcome, TransitionResult):
            return outcome
        from_phase = self._phase
        candidate = self.candidate
        if candidate.tries_remaining <= 0:
            return self._refuse("boot_attempt",
                                "candidate has no attempts remaining")
        self._set_slot(
            self._to_slot,
            tries_remaining=candidate.tries_remaining - 1,
            state="trying",
            currently_running=True,
        )
        self._set_slot(self._from_slot, currently_running=False)
        self._phase = outcome.to_phase
        self._assert_invariants()
        return TransitionResult(True, "boot_attempt", from_phase, self._phase,
                                f"attempt {len(self._attempts) + 1} of {self.max_attempts}")

    def probe_pass(self, detail: str | None = None) -> TransitionResult:
        """The health probe passed. Promote — and retain the payload replaced.

        The previous active keeps ``successful`` and a non-zero priority. It
        becomes the candidate (the next write target) but stays bootable until it
        is actually overwritten, so the target is never one bad boot away from
        having nowhere to go.
        """
        outcome = self._offer("probe_pass")
        if isinstance(outcome, TransitionResult):
            return outcome
        from_phase = self._phase
        self._record_attempt("pass", None, detail)

        old_active, new_active = self._from_slot, self._to_slot
        self._set_slot(new_active, role="active", state="good", successful=True,
                       boot_priority=15, tries_remaining=0, last_probe_verdict="pass")
        self._set_slot(old_active, role="candidate", boot_priority=14)
        # The preserved-digest invariant is scoped to the transaction's fromSlot,
        # which promotion does not overwrite — it only re-roles it.
        self._settle("promoted", None)
        return TransitionResult(True, "probe_pass", from_phase, self._phase,
                                f"promoted slot {new_active}; slot {old_active} retained "
                                "bootable as the fallback")

    def probe_fail(self, detail: str = "health probe failed") -> TransitionResult:
        return self._fail_attempt("probe_fail", "probe-failed", detail)

    def watchdog_expired(self, detail: str = "watchdog expired",
                         *, expiry_action: str = "reboot") -> TransitionResult:
        """A watchdog fired mid-boot.

        ``expiry_action="reboot-to-fallback"`` zeroes the candidate's remaining
        tries before resetting, converting a hang into an immediate fallback
        rather than one that costs the full attempt budget. That is the whole
        point of the distinction in the contract's watchdog enum.
        """
        if expiry_action not in {"reboot", "reboot-to-fallback"}:
            raise ValueError(f"unknown expiry_action {expiry_action!r}")
        if expiry_action == "reboot-to-fallback" and self._phase == "trying":
            self._set_slot(self._to_slot, tries_remaining=0)
        return self._fail_attempt("watchdog_expired", "watchdog-expired", detail)

    def operator_abort(self, detail: str = "aborted by operator") -> TransitionResult:
        outcome = self._offer("operator_abort")
        if isinstance(outcome, TransitionResult):
            return outcome
        from_phase = self._phase
        if from_phase == "trying":
            self._record_attempt("aborted", self._from_slot, detail)
        self._mark_candidate_unbootable()
        self._settle("rolled_back", "operator-abort")
        return TransitionResult(True, "operator_abort", from_phase, self._phase,
                                f"{detail}; target returned to slot {self._from_slot}",
                                fell_back_to=self._from_slot)

    # ── shared failure path ────────────────────────────────────────────────────
    def _fail_attempt(self, event: str, result: str, detail: str) -> TransitionResult:
        outcome = self._offer(event)
        if isinstance(outcome, TransitionResult):
            return outcome
        from_phase = self._phase
        self._record_attempt(result, self._from_slot, detail)
        # Every failed attempt returns the target to the known-good slot. This
        # happens on the retry path too, not only on the terminal one: between
        # attempts the target runs the payload that works.
        self._set_slot(self._to_slot, currently_running=False, state="written",
                       last_probe_verdict="fail")
        self._set_slot(self._from_slot, currently_running=True)

        if outcome.to_phase == "refused":
            self._mark_candidate_unbootable()
            self._settle("refused", "attempts-exhausted")
            return TransitionResult(
                True, event, from_phase, self._phase,
                f"{detail}; attempt budget exhausted after {len(self._attempts)} "
                f"attempt(s) — candidate marked unbootable, target stays on slot "
                f"{self._from_slot}",
                fell_back_to=self._from_slot,
            )

        self._phase = outcome.to_phase
        self._assert_invariants()
        return TransitionResult(
            True, event, from_phase, self._phase,
            f"{detail}; rolled back to slot {self._from_slot}, "
            f"{self.candidate.tries_remaining} attempt(s) remaining",
            fell_back_to=self._from_slot,
        )

    def _record_attempt(self, result: str, fell_back_to: str | None,
                        detail: str | None) -> None:
        self._attempts.append(
            AttemptRecord(
                attempt_number=len(self._attempts) + 1,
                started_at=self.clock(),
                result=result,
                fell_back_to=fell_back_to,
                tries_remaining_after=self.candidate.tries_remaining,
                detail=detail,
            )
        )

    def _mark_candidate_unbootable(self) -> None:
        """Zero the candidate's priority and restore the fallback to the ceiling.

        Zeroing is what makes fallback automatic rather than operator-driven:
        without it the selector picks the broken slot again on the next power
        cycle. Restoring the active slot undoes the demotion that arming applied,
        so a target that has survived several failed updates is not left sitting
        at a reduced priority for no reason anyone can reconstruct.
        """
        self._set_slot(self._to_slot, state="unbootable", boot_priority=0,
                       tries_remaining=0, successful=False)
        self._set_slot(self._from_slot, boot_priority=ARMED_CANDIDATE_PRIORITY)
        if not self.slots[self._from_slot].currently_running:
            self._set_slot(self._to_slot, currently_running=False)
            self._set_slot(self._from_slot, currently_running=True)

    def _settle(self, phase: str, reason: str | None) -> None:
        if reason is not None and reason not in ROLLBACK_REASONS:
            raise InvariantViolation(f"unattributed rollback reason {reason!r}")
        self._phase = phase
        self._rollback_reason = reason
        self._settled_at = self.clock()
        self._assert_invariants()

    # ── contract document emission ─────────────────────────────────────────────
    def to_transaction_document(self) -> dict:
        """Render the UpdateTransaction this machine's history describes."""
        if self._phase == "idle":
            raise InvariantViolation(
                "no transaction has been opened — call begin() first"
            )
        settled = self._phase in TERMINAL_PHASES
        doc = {
            "id": self.transaction_id,
            "type": "UpdateTransaction",
            "specVersion": SPEC_VERSION,
            "targetRef": self.target_ref,
            "fromSlot": self._from_slot,
            "toSlot": self._to_slot,
            "preservedPayloadDigest": self._preserved_digest,
            "candidatePayloadDigest": self._candidate_digest,
            "healthProbeRef": self.health_probe_ref,
            "healthProbeDigest": self.health_probe_digest,
            "maxAttempts": self.max_attempts,
            "outcome": PHASE_TO_OUTCOME[self._phase],
            "rollbackReason": self._rollback_reason,
            "openedAt": self._opened_at,
            "settledAt": self._settled_at if settled else None,
            "settledOnSlot": (
                (self._to_slot if self._phase == "promoted" else self._from_slot)
                if settled else None
            ),
            "attempts": [a.to_dict() for a in self._attempts],
        }
        return doc

    def to_slot_documents(self) -> list[dict]:
        """Render both UpdateSlot documents for the target's current state."""
        docs = []
        for label in ("A", "B"):
            slot = self.slots[label]
            doc = {
                "id": f"urn:srcos:update-slot:{_local(self.target_ref)}_{label.lower()}",
                "type": "UpdateSlot",
                "specVersion": SPEC_VERSION,
                "targetRef": self.target_ref,
                "slot": slot.slot,
                "role": slot.role,
                "state": slot.state,
                "bootPriority": slot.boot_priority,
                "triesRemaining": slot.tries_remaining,
                "successful": slot.successful,
                "currentlyRunning": slot.currently_running,
            }
            if slot.payload_digest:
                doc["payloadDigest"] = slot.payload_digest
            if slot.payload_ref:
                doc["payloadRef"] = slot.payload_ref
            if slot.version:
                doc["version"] = slot.version
            if slot.last_probe_verdict:
                doc["lastProbeVerdict"] = slot.last_probe_verdict
            docs.append(doc)
        return docs


# Fields the probe's definitionDigest covers. Normative, and identical to the
# projection in sourceos-spec's tools/validate_ab_update_examples.py: the whole
# check and watchdog objects, descriptions included. An exclusion list is
# somewhere to hide a weakening.
PROBE_DIGEST_FIELDS = (
    "checks",
    "evaluatedIn",
    "minConsecutivePasses",
    "onProbeUnavailable",
    "timeoutSeconds",
    "watchdogs",
)


def probe_definition_digest(probe_doc: dict) -> str:
    """Recompute UpdateHealthProbe.definitionDigest from the probe itself.

    Recomputing rather than reading back is what makes the pin real: if someone
    relaxes a check after a transaction pinned the gate, the digests diverge and
    the caller can refuse. A stored digest is a claim about pinning.
    """
    import hashlib
    import json as _json

    missing = [f for f in PROBE_DIGEST_FIELDS if f not in probe_doc]
    if missing:
        raise ValueError(f"probe document is missing {', '.join(missing)}")
    core = {field_name: probe_doc[field_name] for field_name in PROBE_DIGEST_FIELDS}
    canonical = _json.dumps(core, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _local(urn: str) -> str:
    """Local-id portion of a URN, sanitised for use in a slot URN."""
    tail = urn.rsplit(":", 1)[-1]
    return "".join(c if (c.isalnum() or c in "._~-") else "_" for c in tail)


def slot_from_document(doc: dict) -> SlotState:
    """Build a SlotState from an UpdateSlot document."""
    return SlotState(
        slot=doc["slot"],
        role=doc["role"],
        state=doc["state"],
        boot_priority=doc["bootPriority"],
        tries_remaining=doc["triesRemaining"],
        successful=doc["successful"],
        currently_running=doc["currentlyRunning"],
        payload_digest=doc.get("payloadDigest"),
        payload_ref=doc.get("payloadRef"),
        version=doc.get("version"),
        last_probe_verdict=doc.get("lastProbeVerdict"),
    )


def machine_from_slot_documents(
    docs: list[dict],
    *,
    transaction_id: str,
    health_probe_ref: str,
    health_probe_digest: str,
    max_attempts: int = 2,
    clock: Callable[[], str] = _utc_now,
) -> AbUpdateMachine:
    """Construct a machine from a pair of UpdateSlot documents."""
    if len(docs) != 2:
        raise ValueError("expected exactly two UpdateSlot documents")
    targets = {d["targetRef"] for d in docs}
    if len(targets) != 1:
        raise ValueError("both slots must belong to the same targetRef")
    slots = {d["slot"]: slot_from_document(d) for d in docs}
    return AbUpdateMachine(
        target_ref=targets.pop(),
        transaction_id=transaction_id,
        slots=slots,
        health_probe_ref=health_probe_ref,
        health_probe_digest=health_probe_digest,
        max_attempts=max_attempts,
        clock=clock,
    )
