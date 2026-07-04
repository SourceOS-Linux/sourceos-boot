#!/usr/bin/env python3
"""Lifecycle promotion state-machine engine for SourceOS.

The states and the ``LifecycleStateRecord`` schema already exist (see
``nlboot/schemas/lifecycle-state-record.schema.v0.1.json``). This module is the
ENGINE that ENFORCES the transitions between them: it owns the explicit
allowed-transition table, fail-closed admission of illegal transitions,
approval-required gating, and emission of a schema-conformant
``LifecycleStateRecord`` for every attempted transition (allowed or refused).

Promotion lineage (ReleaseSet.v1 spec, human-facing):

    draft -> built -> signed -> assigned -> deployed -> attested -> compliant
    rollback / supersede are transitions out of a deployed lineage node.

The LifecycleStateRecord schema models ``deployed`` at a finer grain
(planned -> fetched -> loaded -> executed) and adds ``resolved`` between draft
and built. This engine binds the spec lineage onto the schema state enum.

Two binding hook points are exposed but NOT wired to cross-repo services
(referenced by path/comment only, never imported):

  (a) Inception capability lattice floor. A transition attempted under the
      bottom capability (BOTTOM, "⊥") is refused — mirrors the I3 absorption
      invariant of ``prophet-platform/tools/strictempty_kit.py``
      (exec_under(BOTTOM, x) == empty). We do not import it; we re-state the
      one rule we need: bottom capability is strict, so exec is refused.

  (b) Evidence-gated promotion. Transitions that must be backed by externally
      produced evidence (a signed manifest in Katello, a trust-chain admission
      decision) declare their required proofs here. The actual evidence is
      produced by:
        - SociOS-Linux__socios/tools/verify-katello-uploaded-artifacts
          (Katello/Hammer artifact-presence gate)
        - prophet-platform/docs/TRUST_CHAIN_ADMISSION_CONTRACT.md
          (admit_artifact governed admission decision)
      This engine only checks that the named proofs are PRESENT on the request;
      it does not call those services.

Zero-dependency, pure stdlib, side-effect free (no I/O, no subprocess).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "v0.1"
KIND = "LifecycleStateRecord"

# Inception capability lattice floor. Re-stated from strictempty_kit (ADR-036),
# NOT imported, to keep this repo zero-cross-dependency.
BOTTOM_CAPABILITY = "⊥"  # "⊥"

# ── states (must stay a subset of the schema's state enum) ──────────────────────
STATES = {
    "draft",
    "resolved",
    "built",
    "signed",
    "assigned",
    "planned",
    "fetched",
    "loaded",
    "executed",
    "attested",
    "compliant",
    "noncompliant",
    "rollback-available",
    "rolled-back",
    "blocked",
}

# ── transition names (must stay a subset of the schema's transition.name enum) ──
TRANSITIONS = {
    "resolve-bom",
    "build",
    "sign",
    "assign",
    "validate-token",
    "plan",
    "fetch",
    "load-only",
    "execute",
    "attest",
    "evaluate-compliance",
    "mark-rollback-available",
    "rollback",
    "refuse",
}


@dataclass(frozen=True)
class TransitionRule:
    """One legal edge of the state machine.

    from_state -> to_state via a named transition. ``approval_required`` gates
    the most consequential edges (the ones that mutate hosts or commit a
    release to a fleet). ``required_proofs`` are the evidence references that
    MUST be present on the request for the transition to be allowed — this is
    the evidence-gate hook point (b).
    """

    name: str
    from_state: str
    to_state: str
    approval_required: bool = False
    required_proofs: tuple[str, ...] = ()


# ── the explicit allowed-transition table ───────────────────────────────────────
# Spec lineage  draft -> built -> signed -> assigned -> deployed -> attested -> compliant
# is realised on the schema state grain below. "deployed" = plan -> fetch ->
# load-only -> execute. Anything not in this table is illegal (fail-closed).
TRANSITION_TABLE: tuple[TransitionRule, ...] = (
    # ── construction lane ───────────────────────────────────────────────────
    TransitionRule("resolve-bom", "draft", "resolved"),
    TransitionRule(
        "build",
        "resolved",
        "built",
        required_proofs=("sbomRef",),
    ),
    TransitionRule(
        "sign",
        "built",
        "signed",
        approval_required=True,
        # signed manifest must be verifiably published — Katello/Hammer gate.
        required_proofs=("signatureRef", "katelloManifestRef"),
    ),
    # ── assignment lane ──────────────────────────────────────────────────────
    TransitionRule(
        "assign",
        "signed",
        "assigned",
        approval_required=True,
        # admit_artifact governed decision (prophet trust-chain) before fleet bind.
        required_proofs=("trustChainAdmissionRef",),
    ),
    # ── deployment lane (spec "deployed" expands to plan->fetch->load->execute) ─
    TransitionRule("validate-token", "assigned", "assigned"),  # idempotent gate
    TransitionRule("plan", "assigned", "planned"),
    TransitionRule("fetch", "planned", "fetched", required_proofs=("artifactDigestRef",)),
    TransitionRule("load-only", "fetched", "loaded"),
    TransitionRule(
        "execute",
        "loaded",
        "executed",
        approval_required=True,  # host-mutating boundary
    ),
    # ── attestation / compliance lane ────────────────────────────────────────
    TransitionRule("attest", "executed", "attested", required_proofs=("attestationRef",)),
    TransitionRule("evaluate-compliance", "attested", "compliant"),
    TransitionRule("evaluate-compliance", "attested", "noncompliant"),
    # ── rollback lane ────────────────────────────────────────────────────────
    TransitionRule("mark-rollback-available", "compliant", "rollback-available"),
    TransitionRule("mark-rollback-available", "noncompliant", "rollback-available"),
    TransitionRule("mark-rollback-available", "executed", "rollback-available"),
    TransitionRule(
        "rollback",
        "rollback-available",
        "rolled-back",
        approval_required=True,  # host-mutating boundary
    ),
)

# index for O(1) lookup: (from_state, transition_name) -> [rules]
_INDEX: dict[tuple[str, str], list[TransitionRule]] = {}
for _rule in TRANSITION_TABLE:
    _INDEX.setdefault((_rule.from_state, _rule.name), []).append(_rule)


def lookup(from_state: str | None, transition_name: str, to_state: str | None = None):
    """Return the matching TransitionRule, or None if the edge is illegal.

    A transition like ``evaluate-compliance`` has two legal targets
    (compliant / noncompliant); ``to_state`` disambiguates. If ``to_state`` is
    omitted and the edge is unambiguous, the single rule is returned.
    """
    candidates = _INDEX.get((from_state, transition_name), [])
    if not candidates:
        return None
    if to_state is None:
        return candidates[0] if len(candidates) == 1 else None
    for rule in candidates:
        if rule.to_state == to_state:
            return rule
    return None


@dataclass(frozen=True)
class TransitionRequest:
    """An attempt to advance a lifecycle object.

    capability     : the actor's capability token. BOTTOM_CAPABILITY ("⊥")
                     refuses the transition (Inception floor, hook a).
    proofs_present : evidence references the caller asserts are present
                     (evidence-gate hook b).
    approval_ref   : non-None grants approval for approval-required edges.
    """

    transition_name: str
    capability: str = "cap:operator"
    to_state: str | None = None
    proofs_present: tuple[str, ...] = ()
    approval_ref: str | None = None
    policy_ref: str = "urn:srcos:policy:lifecycle:v0.1"
    policy_hash: str = "sha256:" + "0" * 64


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(
    *,
    subject_ref: str,
    object_ref: str,
    object_kind: str,
    from_state: str | None,
    to_state: str,
    transition_name: str,
    allowed: bool,
    reason: str,
    refusal_ref: str | None,
    required_proofs: list[str],
    present_proofs: list[str],
    approval_required: bool,
    approval_ref: str | None,
    policy_ref: str,
    policy_hash: str,
    side_effects: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build a schema-conformant LifecycleStateRecord dict."""
    missing = [p for p in required_proofs if p not in present_proofs]
    rec: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "recordId": f"urn:srcos:lifecycle-state-record:{uuid.uuid4()}",
        "capturedAt": _now(),
        "subjectRef": subject_ref,
        "objectRef": object_ref,
        "objectKind": object_kind,
        "fromState": from_state,
        "toState": to_state,
        "transition": {
            "name": transition_name,
            "allowed": allowed,
            "reason": reason,
            "refusalRef": refusal_ref,
        },
        "proofs": {
            "required": list(required_proofs),
            "present": list(present_proofs),
            "missing": missing,
        },
        "policy": {
            "policyRef": policy_ref,
            "policyHash": policy_hash,
            "approvalRequired": approval_required,
            "approvalRef": approval_ref,
        },
    }
    if side_effects is not None:
        rec["sideEffects"] = side_effects
    return rec


# transitions that cross the host-mutation boundary, for sideEffects emission.
_HOST_MUTATING = {"execute", "rollback"}


def apply_transition(
    record: dict[str, Any],
    request: TransitionRequest,
) -> dict[str, Any]:
    """Apply ``request`` to a lifecycle ``record`` and return a new
    LifecycleStateRecord transition entry (a dict conforming to
    ``lifecycle-state-record.schema.v0.1.json``).

    The input ``record`` carries the current lifecycle position. Required keys:
      - currentState : the from-state (None means initial mint, e.g. -> draft)
      - subjectRef, objectRef, objectKind

    Fail-closed order of checks (the FIRST failure refuses):
      1. Inception capability floor — BOTTOM ("⊥") refuses any transition.
      2. Legality — (from_state, transition, to_state) must be in the table.
      3. Approval gate — approval-required edges need request.approval_ref.
      4. Evidence gate — every required proof must be present.

    Refusals are emitted as a record with transition.name == "refuse",
    transition.allowed == False, and a refusalRef. This function is pure: the
    input ``record`` is never mutated. The caller advances its own state by
    reading ``result["toState"]`` of an allowed (transition.allowed == True)
    record.
    """
    from_state = record.get("currentState")
    subject_ref = record.get("subjectRef", "urn:srcos:actor:unknown")
    object_ref = record.get("objectRef", "urn:srcos:object:unknown")
    object_kind = record.get("objectKind", "ReleaseSet")
    name = request.transition_name

    # ── hook (a): Inception capability lattice floor ────────────────────────
    # Mirrors strictempty_kit.exec_under(BOTTOM, x) == empty. Bottom capability
    # is strict: no transition may execute under it. Fail-closed, first.
    if request.capability == BOTTOM_CAPABILITY:
        return _record(
            subject_ref=subject_ref,
            object_ref=object_ref,
            object_kind=object_kind,
            from_state=from_state,
            to_state="blocked",
            transition_name="refuse",
            allowed=False,
            reason=(
                "capability floor: transition attempted under bottom capability "
                "(⊥); exec under bottom is strict (Inception I3 absorption)"
            ),
            refusal_ref="urn:srcos:refusal:capability-bottom",
            required_proofs=[],
            present_proofs=list(request.proofs_present),
            approval_required=False,
            approval_ref=None,
            policy_ref=request.policy_ref,
            policy_hash=request.policy_hash,
        )

    # ── check 2: legality (fail-closed) ─────────────────────────────────────
    rule = lookup(from_state, name, request.to_state)
    if rule is None:
        return _record(
            subject_ref=subject_ref,
            object_ref=object_ref,
            object_kind=object_kind,
            from_state=from_state,
            to_state="blocked",
            transition_name="refuse",
            allowed=False,
            reason=(
                f"illegal transition: ({from_state!r}, {name!r}"
                + (f", -> {request.to_state!r}" if request.to_state else "")
                + ") is not in the allowed-transition table"
            ),
            refusal_ref="urn:srcos:refusal:illegal-transition",
            required_proofs=[],
            present_proofs=list(request.proofs_present),
            approval_required=False,
            approval_ref=None,
            policy_ref=request.policy_ref,
            policy_hash=request.policy_hash,
        )

    # ── check 3: approval gate ──────────────────────────────────────────────
    if rule.approval_required and not request.approval_ref:
        return _record(
            subject_ref=subject_ref,
            object_ref=object_ref,
            object_kind=object_kind,
            from_state=from_state,
            to_state="blocked",
            transition_name="refuse",
            allowed=False,
            reason=(
                f"approval required: transition {name!r} "
                f"({from_state} -> {rule.to_state}) needs an approvalRef"
            ),
            refusal_ref="urn:srcos:refusal:approval-required",
            required_proofs=list(rule.required_proofs),
            present_proofs=list(request.proofs_present),
            approval_required=True,
            approval_ref=None,
            policy_ref=request.policy_ref,
            policy_hash=request.policy_hash,
        )

    # ── check 4: evidence gate (hook b) ─────────────────────────────────────
    missing = [p for p in rule.required_proofs if p not in request.proofs_present]
    if missing:
        return _record(
            subject_ref=subject_ref,
            object_ref=object_ref,
            object_kind=object_kind,
            from_state=from_state,
            to_state="blocked",
            transition_name="refuse",
            allowed=False,
            reason=(
                f"evidence gate: transition {name!r} missing required proofs "
                f"{missing} (Katello manifest / trust-chain admission produce these)"
            ),
            refusal_ref="urn:srcos:refusal:missing-evidence",
            required_proofs=list(rule.required_proofs),
            present_proofs=list(request.proofs_present),
            approval_required=rule.approval_required,
            approval_ref=request.approval_ref,
            policy_ref=request.policy_ref,
            policy_hash=request.policy_hash,
        )

    # ── allowed ──────────────────────────────────────────────────────────────
    side_effects = None
    if name in _HOST_MUTATING:
        side_effects = {
            "hostMutation": True,
            "diskWrite": True,
            "reboot": name == "execute",
            "networkFetch": False,
        }
    return _record(
        subject_ref=subject_ref,
        object_ref=object_ref,
        object_kind=object_kind,
        from_state=from_state,
        to_state=rule.to_state,
        transition_name=name,
        allowed=True,
        reason=f"transition {name!r} permitted: {from_state} -> {rule.to_state}",
        refusal_ref=None,
        required_proofs=list(rule.required_proofs),
        present_proofs=list(request.proofs_present),
        approval_required=rule.approval_required,
        approval_ref=request.approval_ref,
        policy_ref=request.policy_ref,
        policy_hash=request.policy_hash,
        side_effects=side_effects,
    )
