"""Tests for the lifecycle promotion state-machine engine.

Asserts:
  - full happy-path draft -> ... -> compliant walk, each step allowed + conformant
  - every illegal transition is rejected (fail-closed)
  - an approval-required transition is blocked without an approvalRef, allowed with
  - the rollback path
  - bottom-capability (⊥) refusal (Inception capability floor)
  - emitted records conform to the LifecycleStateRecord v0.1 schema enums/required
"""

from __future__ import annotations

from sourceos_boot.lifecycle_state_machine import (
    BOTTOM_CAPABILITY,
    STATES,
    TRANSITION_TABLE,
    TRANSITIONS,
    TransitionRequest,
    apply_transition,
    lookup,
)

# ── schema mirror (kept in sync with lifecycle-state-record.schema.v0.1.json) ──
SCHEMA_STATES = {
    "draft", "resolved", "built", "signed", "assigned", "planned", "fetched",
    "loaded", "executed", "attested", "compliant", "noncompliant",
    "rollback-available", "rolled-back", "blocked",
}
SCHEMA_TRANSITIONS = {
    "resolve-bom", "build", "sign", "assign", "validate-token", "plan", "fetch",
    "load-only", "execute", "attest", "evaluate-compliance",
    "mark-rollback-available", "rollback", "refuse",
}
SCHEMA_OBJECT_KINDS = {
    "ReleaseSet", "BootReleaseSet", "SignedBootManifest", "EnrollmentToken",
    "BootPlan", "ArtifactCacheRecord", "AdapterRecord", "Fingerprint",
}
RECORD_REQUIRED = {
    "schemaVersion", "kind", "recordId", "capturedAt", "subjectRef", "objectRef",
    "objectKind", "fromState", "toState", "transition", "proofs", "policy",
}


def assert_conformant(rec: dict) -> None:
    """Zero-dependency conformance check against the v0.1 schema essentials."""
    assert RECORD_REQUIRED <= set(rec), f"missing required: {RECORD_REQUIRED - set(rec)}"
    assert rec["schemaVersion"] == "v0.1"
    assert rec["kind"] == "LifecycleStateRecord"
    assert rec["recordId"].startswith("urn:srcos:lifecycle-state-record:")
    assert rec["fromState"] is None or rec["fromState"] in SCHEMA_STATES
    assert rec["toState"] in SCHEMA_STATES
    assert rec["objectKind"] in SCHEMA_OBJECT_KINDS
    t = rec["transition"]
    assert set(t) <= {"name", "allowed", "reason", "refusalRef"}
    assert {"name", "allowed", "reason"} <= set(t)
    assert t["name"] in SCHEMA_TRANSITIONS
    assert isinstance(t["allowed"], bool)
    p = rec["proofs"]
    assert {"required", "present"} <= set(p)
    pol = rec["policy"]
    assert {"policyRef", "policyHash", "approvalRequired"} <= set(pol)
    assert isinstance(pol["approvalRequired"], bool)


def base_record(state, **kw):
    rec = {
        "currentState": state,
        "subjectRef": "urn:srcos:device:m2-demo",
        "objectRef": "urn:srcos:release-set:demo-1",
        "objectKind": "ReleaseSet",
    }
    rec.update(kw)
    return rec


# ── states/transitions stay subsets of the schema ──────────────────────────────

def test_engine_states_subset_of_schema():
    assert STATES <= SCHEMA_STATES


def test_engine_transitions_subset_of_schema():
    assert TRANSITIONS <= SCHEMA_TRANSITIONS


# ── happy path: draft -> ... -> compliant ───────────────────────────────────────

def test_full_happy_path_to_compliant():
    # (transition, to_state, proofs, approval)
    walk = [
        ("resolve-bom", "resolved", (), None),
        ("build", "built", ("sbomRef",), None),
        ("sign", "signed", ("signatureRef", "katelloManifestRef"), "urn:approval:1"),
        ("assign", "assigned", ("trustChainAdmissionRef",), "urn:approval:2"),
        ("plan", "planned", (), None),
        ("fetch", "fetched", ("artifactDigestRef",), None),
        ("load-only", "loaded", (), None),
        ("execute", "executed", (), "urn:approval:3"),
        ("attest", "attested", ("attestationRef",), None),
        ("evaluate-compliance", "compliant", (), None),
    ]
    state = "draft"
    for name, expected_to, proofs, approval in walk:
        req = TransitionRequest(
            transition_name=name,
            to_state=expected_to,
            proofs_present=proofs,
            approval_ref=approval,
        )
        rec = apply_transition(base_record(state), req)
        assert rec["transition"]["allowed"] is True, (name, rec["transition"]["reason"])
        assert rec["toState"] == expected_to
        assert rec["proofs"]["missing"] == []
        assert_conformant(rec)
        state = rec["toState"]
    assert state == "compliant"


def test_execute_emits_host_mutation_side_effects():
    req = TransitionRequest("execute", to_state="executed", approval_ref="urn:a")
    rec = apply_transition(base_record("loaded"), req)
    assert rec["transition"]["allowed"] is True
    assert rec["sideEffects"]["hostMutation"] is True
    assert rec["sideEffects"]["reboot"] is True


# ── illegal transitions: fail-closed for every non-edge ─────────────────────────

def test_every_illegal_transition_is_rejected():
    legal = {(r.from_state, r.name, r.to_state) for r in TRANSITION_TABLE}
    rejected = 0
    for from_state in SCHEMA_STATES:
        for name in (SCHEMA_TRANSITIONS - {"refuse"}):
            # an edge is legal only if some to_state matches; build with the
            # actual rule's to_state when legal, else probe with no to_state.
            edges = [t for (f, n, t) in legal if f == from_state and n == name]
            if edges:
                continue  # this (from, name) has at least one legal target
            req = TransitionRequest(
                transition_name=name,
                proofs_present=("sbomRef", "signatureRef", "katelloManifestRef",
                                "trustChainAdmissionRef", "artifactDigestRef",
                                "attestationRef"),
                approval_ref="urn:approval:any",
            )
            rec = apply_transition(base_record(from_state), req)
            assert rec["transition"]["allowed"] is False
            assert rec["transition"]["name"] == "refuse"
            assert rec["toState"] == "blocked"
            assert rec["transition"]["refusalRef"] == "urn:srcos:refusal:illegal-transition"
            assert_conformant(rec)
            rejected += 1
    assert rejected > 0  # we actually exercised illegal edges


def test_illegal_transition_even_with_full_proofs_and_approval():
    # draft cannot jump straight to execute regardless of proofs/approval
    req = TransitionRequest(
        "execute", to_state="executed",
        proofs_present=("attestationRef",), approval_ref="urn:approval:1",
    )
    rec = apply_transition(base_record("draft"), req)
    assert rec["transition"]["allowed"] is False
    assert rec["transition"]["refusalRef"] == "urn:srcos:refusal:illegal-transition"


# ── approval gate ────────────────────────────────────────────────────────────────

def test_approval_required_blocked_without_approval():
    # sign is approval-required AND evidence-required; approval is checked first
    req = TransitionRequest(
        "sign", to_state="signed",
        proofs_present=("signatureRef", "katelloManifestRef"),
        approval_ref=None,
    )
    rec = apply_transition(base_record("built"), req)
    assert rec["transition"]["allowed"] is False
    assert rec["transition"]["refusalRef"] == "urn:srcos:refusal:approval-required"
    assert rec["policy"]["approvalRequired"] is True
    assert rec["policy"]["approvalRef"] is None
    assert_conformant(rec)


def test_approval_required_allowed_with_approval():
    req = TransitionRequest(
        "sign", to_state="signed",
        proofs_present=("signatureRef", "katelloManifestRef"),
        approval_ref="urn:approval:signer",
    )
    rec = apply_transition(base_record("built"), req)
    assert rec["transition"]["allowed"] is True
    assert rec["policy"]["approvalRef"] == "urn:approval:signer"


# ── evidence gate ────────────────────────────────────────────────────────────────

def test_evidence_gate_blocks_when_proofs_missing():
    # build needs sbomRef; supply nothing
    req = TransitionRequest("build", to_state="built", proofs_present=())
    rec = apply_transition(base_record("resolved"), req)
    assert rec["transition"]["allowed"] is False
    assert rec["transition"]["refusalRef"] == "urn:srcos:refusal:missing-evidence"
    assert "sbomRef" in rec["proofs"]["missing"]
    assert_conformant(rec)


# ── rollback path ────────────────────────────────────────────────────────────────

def test_rollback_path():
    # compliant -> mark-rollback-available -> rollback (approval-required)
    rec1 = apply_transition(
        base_record("compliant"),
        TransitionRequest("mark-rollback-available", to_state="rollback-available"),
    )
    assert rec1["transition"]["allowed"] is True
    assert rec1["toState"] == "rollback-available"

    # rollback without approval is blocked
    rec2 = apply_transition(
        base_record("rollback-available"),
        TransitionRequest("rollback", to_state="rolled-back"),
    )
    assert rec2["transition"]["allowed"] is False
    assert rec2["transition"]["refusalRef"] == "urn:srcos:refusal:approval-required"

    # rollback with approval succeeds and is host-mutating
    rec3 = apply_transition(
        base_record("rollback-available"),
        TransitionRequest("rollback", to_state="rolled-back", approval_ref="urn:approval:ops"),
    )
    assert rec3["transition"]["allowed"] is True
    assert rec3["toState"] == "rolled-back"
    assert rec3["sideEffects"]["hostMutation"] is True
    assert_conformant(rec3)


def test_noncompliant_can_also_mark_rollback_available():
    rec = apply_transition(
        base_record("noncompliant"),
        TransitionRequest("mark-rollback-available", to_state="rollback-available"),
    )
    assert rec["transition"]["allowed"] is True
    assert rec["toState"] == "rollback-available"


# ── Inception capability floor (⊥) ──────────────────────────────────────────────

def test_bottom_capability_refuses_any_transition():
    # even a fully-legal, fully-approved, fully-proven transition is refused
    req = TransitionRequest(
        "sign", to_state="signed",
        capability=BOTTOM_CAPABILITY,
        proofs_present=("signatureRef", "katelloManifestRef"),
        approval_ref="urn:approval:signer",
    )
    rec = apply_transition(base_record("built"), req)
    assert rec["transition"]["allowed"] is False
    assert rec["transition"]["name"] == "refuse"
    assert rec["toState"] == "blocked"
    assert rec["transition"]["refusalRef"] == "urn:srcos:refusal:capability-bottom"
    assert_conformant(rec)


def test_bottom_capability_checked_before_legality():
    # an otherwise-illegal transition under ⊥ still refuses with the capability ref,
    # proving the floor is the first (outermost) gate.
    req = TransitionRequest("execute", capability=BOTTOM_CAPABILITY)
    rec = apply_transition(base_record("draft"), req)
    assert rec["transition"]["refusalRef"] == "urn:srcos:refusal:capability-bottom"


# ── lookup disambiguation ────────────────────────────────────────────────────────

def test_lookup_ambiguous_requires_to_state():
    # evaluate-compliance from attested has two targets; without to_state -> None
    assert lookup("attested", "evaluate-compliance") is None
    assert lookup("attested", "evaluate-compliance", "compliant").to_state == "compliant"
    assert lookup("attested", "evaluate-compliance", "noncompliant").to_state == "noncompliant"
