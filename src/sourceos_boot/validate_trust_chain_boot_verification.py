#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas" / "trust-chain-boot-verification-evidence.v0.1.schema.json"
VALID_FIXTURE = ROOT / "examples" / "trust-chain-boot-verification.valid.json"
BLOCKED_FIXTURE = ROOT / "examples" / "trust-chain-boot-verification.blocked.json"


class ValidationError(Exception):
    pass


def fail(message: str) -> None:
    raise ValidationError(message)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def type_matches(value: Any, expected: str) -> bool:
    actual = json_type_name(value)
    if expected == "number":
        return actual in {"integer", "number"}
    return actual == expected


def validate_schema(schema: dict[str, Any], value: Any, path: str = "$") -> None:
    if "const" in schema and value != schema["const"]:
        fail(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{path}: {value!r} not in enum {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(type_matches(value, item) for item in expected_types):
            fail(f"{path}: expected type {expected_types!r}, got {json_type_name(value)!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                fail(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                fail(f"{path}: unexpected properties {extra!r}")
        additional = schema.get("additionalProperties")
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is None and isinstance(additional, dict):
                child_schema = additional
            if child_schema is not None:
                validate_schema(child_schema, item, f"{path}.{key}")

    if isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                validate_schema(item_schema, item, f"{path}[{index}]")


def validate_verified(record: dict[str, Any], path: Path) -> None:
    if record.get("decision") != "verified":
        fail(f"{path}: verified fixture must have decision=verified")
    if record.get("device_claim", {}).get("claim_status") != "verified":
        fail(f"{path}: verified fixture requires verified device claim")
    if not record.get("device_claim", {}).get("claim_ref"):
        fail(f"{path}: verified fixture requires claim_ref")
    if not record.get("boot", {}).get("manifest_hash"):
        fail(f"{path}: verified fixture requires manifest_hash")
    if record.get("verification", {}).get("verification_result") != "pass":
        fail(f"{path}: verified fixture requires verification_result=pass")
    if not record.get("verification", {}).get("attestation_ref"):
        fail(f"{path}: verified fixture requires attestation_ref")
    refs = record.get("trust_chain_refs", {})
    for key in ("policy_profile_ref", "admission_decision_ref", "runtime_receipt_ref"):
        if not refs.get(key):
            fail(f"{path}: verified fixture missing trust_chain_refs.{key}")
    effects = record.get("effects", {})
    if effects.get("boot_allowed") is not True or effects.get("install_allowed") is not True:
        fail(f"{path}: verified fixture must allow boot and install")
    if effects.get("repair_required") is not False or effects.get("human_review_required") is not False:
        fail(f"{path}: verified fixture must not require repair or review")


def validate_blocked(record: dict[str, Any], path: Path) -> None:
    if record.get("decision") != "blocked":
        fail(f"{path}: blocked fixture must have decision=blocked")
    if record.get("device_claim", {}).get("claim_status") == "verified":
        fail(f"{path}: blocked fixture must not have verified claim_status")
    if record.get("boot", {}).get("manifest_hash") is not None:
        fail(f"{path}: blocked fixture should not carry manifest_hash")
    if record.get("verification", {}).get("verification_result") == "pass":
        fail(f"{path}: blocked fixture must not have verification_result=pass")
    effects = record.get("effects", {})
    if effects.get("boot_allowed") is not False or effects.get("install_allowed") is not False:
        fail(f"{path}: blocked fixture must deny boot and install")
    if effects.get("repair_required") is not True or effects.get("human_review_required") is not True:
        fail(f"{path}: blocked fixture must require repair and human review")
    remediation = record.get("remediation", [])
    if not isinstance(remediation, list) or not remediation:
        fail(f"{path}: blocked fixture requires remediation")
    for item in remediation:
        if item.get("required_before_admission") is not True:
            fail(f"{path}: remediation must be required before admission")
        if not item.get("authority"):
            fail(f"{path}: remediation requires authority")


def validate_file(path: Path) -> None:
    schema = load_json(SCHEMA)
    record = load_json(path)
    validate_schema(schema, record)
    if record.get("decision") == "verified":
        validate_verified(record, path)
    elif record.get("decision") == "blocked":
        validate_blocked(record, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Trust Chain boot verification evidence")
    parser.add_argument("paths", nargs="*", type=Path, default=[VALID_FIXTURE, BLOCKED_FIXTURE])
    args = parser.parse_args(argv)

    failed = False
    for path in args.paths:
        try:
            validate_file(path)
            print(f"PASS {path}")
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"FAIL {path}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
