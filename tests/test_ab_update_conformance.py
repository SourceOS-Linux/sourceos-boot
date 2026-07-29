"""The state machine's output must satisfy the contract it claims to implement.

Every document this repo emits is validated against the vendored sourceos-spec
schemas under `schemas/ab-update/`. Without this, "reference implementation of
the A/B fallback update contract" would be a claim in a docstring: the machine
could drift from the schema in either direction and nothing would notice.

Also drives the `sourceos-boot ab-update` CLI, because a library the CLI cannot
reach is a library nobody calls — the failure mode this repo has already met
once (`plan-control-plane` was written and its door was not).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from sourceos_boot.ab_update_machine import (
    AbUpdateMachine,
    SlotState,
    probe_definition_digest,
)
from sourceos_boot.cli import main

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "ab-update"
EXAMPLES = ROOT / "examples" / "ab-update"

GOOD = "sha256:" + "a" * 64
BAD = "sha256:" + "b" * 64
OTHER = "sha256:" + "c" * 64


def schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate(doc: dict, schema_name: str) -> None:
    jsonschema.Draft202012Validator(schema(schema_name)).validate(doc)


def machine(max_attempts: int = 2) -> AbUpdateMachine:
    return AbUpdateMachine(
        target_ref="urn:srcos:node:test_node",
        transaction_id="urn:srcos:update-transaction:test_0001",
        slots={
            "A": SlotState("A", "active", "good", 15, 0, True, True,
                           payload_digest=GOOD, version="1.0.0"),
            "B": SlotState("B", "candidate", "empty", 1, 0, False, False),
        },
        health_probe_ref="urn:srcos:update-health-probe:sourceos_node_v1",
        health_probe_digest="sha256:" + "d" * 64,
        max_attempts=max_attempts,
        clock=lambda: "2026-07-29T10:00:00Z",
    )


# ── the vendored schemas are themselves valid ─────────────────────────────────


@pytest.mark.parametrize(
    "name", ["UpdateSlot.json", "UpdateTransaction.json", "UpdateHealthProbe.json"]
)
def test_vendored_schema_is_a_valid_draft(name: str) -> None:
    jsonschema.Draft202012Validator.check_schema(schema(name))


def test_vendored_probe_example_digest_recomputes() -> None:
    """The digest algorithm here and in sourceos-spec's validator must agree, or
    a probe pinned by one is rejected by the other."""
    probe = json.loads((EXAMPLES / "health-probe.example.json").read_text(encoding="utf-8"))
    validate(probe, "UpdateHealthProbe.json")
    assert probe["definitionDigest"] == probe_definition_digest(probe)


# ── emitted documents conform, on every terminal path ─────────────────────────


def test_pending_transaction_document_conforms() -> None:
    m = machine()
    m.begin(BAD, version="2.0.0")
    doc = m.to_transaction_document()
    validate(doc, "UpdateTransaction.json")
    assert doc["outcome"] == "pending"
    assert doc["settledAt"] is None
    assert doc["settledOnSlot"] is None
    assert doc["fromSlot"] != doc["toSlot"]


def test_refused_transaction_document_conforms_and_settles_on_the_active_slot() -> None:
    m = machine()
    m.begin(BAD)
    m.write_complete(BAD)
    m.arm()
    for _ in range(2):
        m.boot_attempt()
        m.probe_fail("still failing")

    doc = m.to_transaction_document()
    validate(doc, "UpdateTransaction.json")
    assert doc["outcome"] == "refused"
    assert doc["rollbackReason"] == "attempts-exhausted"
    assert doc["settledOnSlot"] == doc["fromSlot"] == "A"
    assert doc["preservedPayloadDigest"] == GOOD
    assert all(a["fellBackTo"] == "A" for a in doc["attempts"])
    assert [a["attemptNumber"] for a in doc["attempts"]] == [1, 2]


def test_promoted_transaction_document_conforms_and_settles_on_the_candidate() -> None:
    m = machine()
    m.begin(BAD)
    m.write_complete(BAD)
    m.arm()
    m.boot_attempt()
    m.probe_pass()

    doc = m.to_transaction_document()
    validate(doc, "UpdateTransaction.json")
    assert doc["outcome"] == "promoted"
    assert doc["rollbackReason"] is None
    assert doc["settledOnSlot"] == doc["toSlot"] == "B"
    # Even on the successful path, the good slot was not overwritten.
    assert doc["preservedPayloadDigest"] == GOOD


def test_rolled_back_transaction_document_conforms() -> None:
    m = machine()
    m.begin(BAD)
    m.write_complete(OTHER)          # digest mismatch
    doc = m.to_transaction_document()
    validate(doc, "UpdateTransaction.json")
    assert doc["outcome"] == "rolled-back"
    assert doc["rollbackReason"] == "digest-mismatch"
    assert doc["settledOnSlot"] == "A"


@pytest.mark.parametrize("path", ["refused", "promoted", "pending", "mismatch"])
def test_slot_documents_conform_on_every_path(path: str) -> None:
    m = machine()
    m.begin(BAD)
    if path == "mismatch":
        m.write_complete(OTHER)
    elif path != "pending":
        m.write_complete(BAD)
        m.arm()
        m.boot_attempt()
        if path == "promoted":
            m.probe_pass()
        else:
            m.probe_fail()
            m.boot_attempt()
            m.probe_fail()

    docs = m.to_slot_documents()
    assert len(docs) == 2
    for doc in docs:
        validate(doc, "UpdateSlot.json")
    assert sum(1 for d in docs if d["role"] == "active") == 1
    assert sum(1 for d in docs if d["currentlyRunning"]) == 1


def test_a_transaction_document_cannot_be_rendered_before_it_is_opened() -> None:
    from sourceos_boot.ab_update_machine import InvariantViolation

    with pytest.raises(InvariantViolation, match="no transaction has been opened"):
        machine().to_transaction_document()


# ── the CLI reaches the library ───────────────────────────────────────────────


def test_cli_ab_update_plan_emits_conformant_documents(capsys) -> None:
    rc = main([
        "ab-update", "plan",
        "--slots", str(EXAMPLES / "slots.example.json"),
        "--probe", str(EXAMPLES / "health-probe.example.json"),
        "--transaction-id", "urn:srcos:update-transaction:fog_edge_07_0042",
        "--payload-digest", "sha256:" + "9" * 64,
        "--version", "2026.07.4",
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "AbUpdatePlan"
    assert out["execute"] is False
    # The plan writes B and preserves A, derived — not supplied by the caller.
    assert out["preservedSlot"] == "A"
    assert out["writeTarget"] == "B"
    validate(out["transaction"], "UpdateTransaction.json")
    for slot_doc in out["plannedSlots"]:
        validate(slot_doc, "UpdateSlot.json")


def test_cli_ab_update_replay_refused_path_stays_on_the_active_slot(capsys) -> None:
    rc = main([
        "ab-update", "replay",
        "--slots", str(EXAMPLES / "slots.example.json"),
        "--probe", str(EXAMPLES / "health-probe.example.json"),
        "--transaction-id", "urn:srcos:update-transaction:fog_edge_07_0042",
        "--events", str(EXAMPLES / "events.refused.example.json"),
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "AbUpdateReplay"
    assert out["finalPhase"] == "refused"

    validate(out["transaction"], "UpdateTransaction.json")
    for slot_doc in out["slots"]:
        validate(slot_doc, "UpdateSlot.json")

    # The trailing retry in the fixture must be reported as refused, not dropped.
    assert out["transitions"][-1]["allowed"] is False
    assert "terminal" in out["transitions"][-1]["reason"]

    active = next(s for s in out["slots"] if s["role"] == "active")
    assert active["slot"] == "A"
    assert active["currentlyRunning"] is True
    assert active["payloadDigest"] == out["transaction"]["preservedPayloadDigest"]
    candidate = next(s for s in out["slots"] if s["role"] == "candidate")
    assert candidate["state"] == "unbootable"
    assert candidate["bootPriority"] == 0


def test_cli_ab_update_replay_promoted_path(capsys) -> None:
    rc = main([
        "ab-update", "replay",
        "--slots", str(EXAMPLES / "slots.example.json"),
        "--probe", str(EXAMPLES / "health-probe.example.json"),
        "--transaction-id", "urn:srcos:update-transaction:fog_edge_07_0043",
        "--events", str(EXAMPLES / "events.promoted.example.json"),
    ])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["finalPhase"] == "promoted"
    validate(out["transaction"], "UpdateTransaction.json")
    assert out["transaction"]["settledOnSlot"] == "B"
    # The replaced payload is retained bootable.
    old = next(s for s in out["slots"] if s["slot"] == "A")
    assert old["successful"] is True
    assert old["bootPriority"] > 0


def test_cli_refuses_a_probe_whose_digest_no_longer_matches_its_checks(tmp_path, capsys) -> None:
    """The gate cannot be relaxed after it is pinned without the CLI noticing."""
    probe = json.loads((EXAMPLES / "health-probe.example.json").read_text(encoding="utf-8"))
    probe["minConsecutivePasses"] = 1                 # weaken the gate
    weakened = tmp_path / "weakened-probe.json"
    weakened.write_text(json.dumps(probe), encoding="utf-8")

    rc = main([
        "ab-update", "plan",
        "--slots", str(EXAMPLES / "slots.example.json"),
        "--probe", str(weakened),
        "--transaction-id", "urn:srcos:update-transaction:fog_edge_07_0044",
        "--payload-digest", "sha256:" + "9" * 64,
    ])
    assert rc == 2
    assert "stale definitionDigest" in capsys.readouterr().err


def test_ab_update_is_a_registered_subcommand() -> None:
    """Guards the failure mode this repo has already met: a planner written and
    its door not registered."""
    from sourceos_boot.cli import build_parser

    actions = [a for a in build_parser()._actions if hasattr(a, "choices") and a.choices]
    commands = set(actions[0].choices)
    assert "ab-update" in commands
