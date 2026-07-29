"""Tests for the A/B dual-slot update state machine.

The three rollback paths the contract exists to guarantee each have a test that
drives the machine to the failure and asserts on the resulting slot state, not
just on the phase name:

  * candidate fails its health probe  -> falls back to the active slot
  * a watchdog expires mid-boot       -> falls back to the active slot
  * two consecutive failures          -> refuses further attempts, stays active

Every test also asserts the invariant that makes those paths meaningful: the
active slot still holds the payload it held when the transaction opened.
"""

from __future__ import annotations

import itertools

import pytest

from sourceos_boot.ab_update_machine import (
    AbUpdateMachine,
    InvariantViolation,
    SlotState,
    machine_from_slot_documents,
)

GOOD = "sha256:" + "a" * 64
BAD = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64
PROBE = "urn:srcos:update-health-probe:sourceos_node_v1"
PROBE_DIGEST = "sha256:" + "d" * 64


def _clock():
    counter = itertools.count()
    return lambda: f"2026-07-29T10:{next(counter):02d}:00Z"


def machine(max_attempts: int = 2) -> AbUpdateMachine:
    """A target sitting on a proven payload in slot A, empty candidate in B."""
    return AbUpdateMachine(
        target_ref="urn:srcos:node:test_node",
        transaction_id="urn:srcos:update-transaction:test_0001",
        slots={
            "A": SlotState("A", "active", "good", 15, 0, True, True,
                           payload_digest=GOOD, version="1.0.0"),
            "B": SlotState("B", "candidate", "empty", 1, 0, False, False),
        },
        health_probe_ref=PROBE,
        health_probe_digest=PROBE_DIGEST,
        max_attempts=max_attempts,
        clock=_clock(),
    )


def armed(max_attempts: int = 2) -> AbUpdateMachine:
    """A machine driven to the point where the candidate is armed and booting."""
    m = machine(max_attempts)
    m.begin(BAD, version="2.0.0")
    m.write_complete(BAD)
    m.arm()
    return m


# ── the write target is derived, never chosen ──────────────────────────────────


def test_begin_writes_the_candidate_and_never_the_active_slot():
    m = machine()
    m.begin(BAD)
    assert m._to_slot == "B"
    assert m.slots["B"].state == "writing"
    # The whole point: A is untouched.
    assert m.slots["A"].payload_digest == GOOD
    assert m.slots["A"].state == "good"
    assert m.slots["A"].successful is True


def test_begin_takes_no_parameter_naming_the_destination_slot():
    """A caller cannot aim an update at the slot it falls back to, because there
    is no argument by which it could."""
    import inspect

    params = set(inspect.signature(AbUpdateMachine.begin).parameters)
    assert not (params & {"to_slot", "slot", "target_slot", "destination"})


def test_a_target_whose_active_slot_is_not_successful_cannot_start_an_update():
    with pytest.raises(InvariantViolation, match="nothing known-good to fall back to"):
        AbUpdateMachine(
            target_ref="urn:srcos:node:test_node",
            transaction_id="urn:srcos:update-transaction:test_0001",
            slots={
                "A": SlotState("A", "active", "written", 15, 1, False, True,
                               payload_digest=GOOD),
                "B": SlotState("B", "candidate", "empty", 1, 0, False, False),
            },
            health_probe_ref=PROBE,
            health_probe_digest=PROBE_DIGEST,
        )


def test_overwriting_the_good_slot_raises_rather_than_returning_an_error():
    """The invariant is enforced by the library, not only by its tests."""
    m = machine()
    m.begin(BAD)
    m.slots["A"] = m.slots["A"].__class__(**{**m.slots["A"].__dict__, "payload_digest": OTHER})
    with pytest.raises(InvariantViolation, match="the good slot was overwritten"):
        m._assert_invariants()


# ── rollback path 1: the candidate fails its probe ─────────────────────────────


def test_probe_failure_rolls_back_to_the_active_slot():
    m = armed()
    m.boot_attempt()
    assert m.running.slot == "B"          # the candidate is executing
    assert m.active.slot == "A"           # but A is still the fallback

    result = m.probe_fail("local-api-200 returned 503")
    assert result.allowed
    assert result.fell_back_to == "A"
    assert m.phase == "armed"             # budget remains, so a retry is legal
    assert m.running.slot == "A"          # and meanwhile the target runs what works
    assert m.slots["A"].payload_digest == GOOD
    assert m.slots["B"].last_probe_verdict == "fail"
    assert m.attempts[-1].result == "probe-failed"
    assert m.attempts[-1].fell_back_to == "A"


# ── rollback path 2: a watchdog expires mid-boot ───────────────────────────────


def test_watchdog_expiry_rolls_back_to_the_active_slot():
    m = armed()
    m.boot_attempt()
    result = m.watchdog_expired("software watchdog: no pet for 90s")
    assert result.allowed
    assert result.fell_back_to == "A"
    assert m.running.slot == "A"
    assert m.slots["A"].payload_digest == GOOD
    assert m.attempts[-1].result == "watchdog-expired"


def test_reboot_to_fallback_watchdog_spends_the_whole_budget_at_once():
    """expiryAction 'reboot-to-fallback' zeroes remaining tries, so a hang becomes
    an immediate fallback rather than one that costs the full attempt budget."""
    m = armed(max_attempts=3)
    m.boot_attempt()
    result = m.watchdog_expired("kernel wedged", expiry_action="reboot-to-fallback")
    assert result.allowed
    assert m.phase == "refused"
    assert m.rollback_reason == "attempts-exhausted"
    assert m.running.slot == "A"
    assert m.slots["B"].boot_priority == 0


# ── rollback path 3: two consecutive failures refuse further attempts ──────────


def test_two_consecutive_failures_refuse_further_attempts_and_stay_on_active():
    m = armed(max_attempts=2)

    m.boot_attempt()
    assert m.slots["B"].tries_remaining == 1      # spent by the bootloader
    m.probe_fail("supervisor-alive never reached ready")
    assert m.phase == "armed"

    m.boot_attempt()
    assert m.slots["B"].tries_remaining == 0
    result = m.probe_fail("supervisor-alive never reached ready")

    assert result.allowed
    assert m.phase == "refused"
    assert m.rollback_reason == "attempts-exhausted"
    # The candidate is marked so the selector will never choose it again. Without
    # this write, fallback is operator-driven and the next power cycle loops.
    assert m.slots["B"].state == "unbootable"
    assert m.slots["B"].boot_priority == 0
    assert m.slots["B"].successful is False
    # And the target is on the payload that works, unchanged.
    assert m.running.slot == "A"
    assert m.active.slot == "A"
    assert m.slots["A"].payload_digest == GOOD
    assert len(m.attempts) == 2


def test_a_refused_transaction_refuses_every_further_event_no_boot_loop():
    """This is the assertion that 'refuse further attempts' actually holds. A
    machine that merely stopped retrying on its own would still loop under a
    caller that retries."""
    m = armed()
    for _ in range(2):
        m.boot_attempt()
        m.probe_fail("still failing")
    assert m.phase == "refused"

    for event in (m.boot_attempt, m.arm, m.probe_pass, m.probe_fail, m.watchdog_expired):
        result = event()
        assert result.allowed is False
        assert "terminal" in result.reason
    assert m.phase == "refused"
    assert len(m.attempts) == 2
    assert m.running.slot == "A"


def test_refusal_restores_the_fallback_to_top_priority():
    """Arming demotes the active slot so the candidate can outrank it. Refusal
    must undo that, or a target that survives several failed updates is left at
    a reduced priority for no reason anyone can reconstruct."""
    m = armed()
    assert m.slots["A"].boot_priority == 14      # demoted by arm()
    for _ in range(2):
        m.boot_attempt()
        m.probe_fail()
    assert m.phase == "refused"
    assert m.slots["A"].boot_priority == 15
    assert m.slots["B"].boot_priority == 0


def test_a_single_attempt_budget_refuses_after_one_failure():
    m = armed(max_attempts=1)
    m.boot_attempt()
    m.probe_fail("failed on the only attempt allowed")
    assert m.phase == "refused"
    assert m.slots["B"].boot_priority == 0
    assert m.running.slot == "A"


# ── the attempt counter ────────────────────────────────────────────────────────


def test_the_attempt_is_spent_by_boot_attempt_not_by_the_probe_result():
    """A payload that hangs before userspace never reports anything, so a counter
    decremented on a result would never terminate for it."""
    m = armed(max_attempts=2)
    assert m.slots["B"].tries_remaining == 2
    m.boot_attempt()
    assert m.slots["B"].tries_remaining == 1     # spent before the payload ran
    m.probe_fail()
    assert m.slots["B"].tries_remaining == 1     # the result did not spend one


def test_boot_attempt_is_refused_once_the_budget_is_gone():
    m = armed(max_attempts=1)
    m.boot_attempt()
    assert m.slots["B"].tries_remaining == 0
    m.probe_fail()
    result = m.boot_attempt()
    assert result.allowed is False


def test_max_attempts_is_bounded_by_the_gpt_tries_width():
    with pytest.raises(ValueError, match="GPT tries width"):
        machine(max_attempts=99)


# ── write-time failures ────────────────────────────────────────────────────────


def test_digest_mismatch_rolls_back_and_never_arms():
    m = machine()
    m.begin(BAD)
    result = m.write_complete(OTHER)
    assert result.allowed
    assert m.phase == "rolled_back"
    assert m.rollback_reason == "digest-mismatch"
    assert m.slots["B"].state == "unbootable"
    assert m.slots["A"].payload_digest == GOOD
    assert m.running.slot == "A"
    assert m.attempts == ()               # it never booted, so there is no attempt


def test_write_failure_leaves_the_active_slot_untouched():
    m = machine()
    m.begin(BAD)
    m.write_failed("no space left on device")
    assert m.phase == "rolled_back"
    assert m.rollback_reason == "write-failed"
    assert m.slots["A"].payload_digest == GOOD
    assert m.slots["A"].successful is True


def test_operator_abort_returns_the_target_to_the_active_slot():
    m = armed()
    m.boot_attempt()
    result = m.operator_abort("operator cancelled the rollout")
    assert result.allowed
    assert m.phase == "rolled_back"
    assert m.rollback_reason == "operator-abort"
    assert m.running.slot == "A"
    assert m.attempts[-1].result == "aborted"


# ── promotion ──────────────────────────────────────────────────────────────────


def test_promotion_swaps_roles_and_retains_the_payload_it_replaced():
    m = armed()
    m.boot_attempt()
    result = m.probe_pass("all blocking checks passed twice")

    assert result.allowed
    assert m.phase == "promoted"
    assert m.rollback_reason is None
    assert m.active.slot == "B"
    assert m.running.slot == "B"
    assert m.slots["B"].successful is True
    assert m.slots["B"].state == "good"

    # The replaced payload stays bootable. Promotion is the moment the previous
    # payload stops being guaranteed, so it must not also be the moment it is
    # thrown away.
    assert m.slots["A"].role == "candidate"
    assert m.slots["A"].successful is True
    assert m.slots["A"].boot_priority > 0
    assert m.slots["A"].payload_digest == GOOD


def test_a_promoted_transaction_refuses_further_events():
    m = armed()
    m.boot_attempt()
    m.probe_pass()
    assert m.probe_fail().allowed is False
    assert m.boot_attempt().allowed is False
    assert m.phase == "promoted"


def test_nothing_reaches_promoted_except_a_passing_probe():
    from sourceos_boot.ab_update_machine import TRANSITION_TABLE

    into_promoted = [r for r in TRANSITION_TABLE if r.to_phase == "promoted"]
    assert [r.event for r in into_promoted] == ["probe_pass"]


# ── fail-closed admission ──────────────────────────────────────────────────────


def test_events_illegal_in_the_current_phase_are_refused():
    m = machine()
    for event in (m.probe_pass, m.probe_fail, m.boot_attempt, m.arm):
        result = event()
        assert result.allowed is False
        assert "illegal in phase 'idle'" in result.reason
    assert m.phase == "idle"


def test_a_candidate_cannot_be_armed_before_its_write_is_verified():
    m = machine()
    m.begin(BAD)
    assert m.arm().allowed is False
    assert m.phase == "writing"


def test_arming_never_certifies_it_only_raises_priority():
    m = machine()
    m.begin(BAD)
    m.write_complete(BAD)
    m.arm()
    assert m.slots["B"].successful is False
    assert m.slots["B"].boot_priority > m.slots["A"].boot_priority
    assert m.slots["B"].tries_remaining == 2


def test_arming_a_target_already_at_the_priority_ceiling_leaves_no_tie():
    """Regression: arming used to RAISE the candidate toward the active slot,
    which has no headroom when the active slot is already at 15. Both ended up
    equal and slot selection was undefined — the bootloader would pick one of two
    payloads with nothing in the recorded state saying which."""
    m = machine()
    assert m.slots["A"].boot_priority == 15      # already at the ceiling
    m.begin(BAD)
    m.write_complete(BAD)
    m.arm()
    assert m.slots["B"].boot_priority > m.slots["A"].boot_priority
    # and the fallback is still selectable, which is the other half of it
    assert m.slots["A"].boot_priority >= 1
    assert m.slots["A"].successful is True
    assert m.slots["A"].payload_digest == GOOD


def test_a_priority_tie_while_on_trial_raises():
    m = armed()
    m.boot_attempt()
    m.slots["B"] = m.slots["B"].__class__(
        **{**m.slots["B"].__dict__, "boot_priority": m.slots["A"].boot_priority}
    )
    with pytest.raises(InvariantViolation, match="tie or inversion"):
        m._assert_invariants()


def test_an_unattributed_rollback_reason_is_rejected():
    m = armed()
    with pytest.raises(InvariantViolation, match="unattributed rollback reason"):
        m._settle("rolled_back", "because-i-said-so")


# ── construction ───────────────────────────────────────────────────────────────


def test_a_target_must_have_exactly_two_slots():
    with pytest.raises(ValueError, match="exactly two slots"):
        AbUpdateMachine(
            target_ref="urn:srcos:node:test_node",
            transaction_id="urn:srcos:update-transaction:t",
            slots={"A": SlotState("A", "active", "good", 15, 0, True, True,
                                  payload_digest=GOOD)},
            health_probe_ref=PROBE,
            health_probe_digest=PROBE_DIGEST,
        )


def test_exactly_one_slot_may_be_active():
    with pytest.raises(ValueError, match="exactly one slot must hold role 'active'"):
        AbUpdateMachine(
            target_ref="urn:srcos:node:test_node",
            transaction_id="urn:srcos:update-transaction:t",
            slots={
                "A": SlotState("A", "active", "good", 15, 0, True, True, payload_digest=GOOD),
                "B": SlotState("B", "active", "good", 14, 0, True, False, payload_digest=BAD),
            },
            health_probe_ref=PROBE,
            health_probe_digest=PROBE_DIGEST,
        )


def test_machine_round_trips_through_updateslot_documents():
    m = armed()
    m.boot_attempt()
    m.probe_pass()
    docs = m.to_slot_documents()

    rebuilt = machine_from_slot_documents(
        docs,
        transaction_id="urn:srcos:update-transaction:test_0002",
        health_probe_ref=PROBE,
        health_probe_digest=PROBE_DIGEST,
    )
    assert rebuilt.active.slot == "B"
    assert rebuilt.candidate.slot == "A"
    # The next update writes A — the slot that is no longer the fallback.
    rebuilt.begin(OTHER)
    assert rebuilt._to_slot == "A"
    assert rebuilt.slots["B"].payload_digest == BAD
