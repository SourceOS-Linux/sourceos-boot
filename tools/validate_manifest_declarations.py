#!/usr/bin/env python3
"""Every declaration in .sourceos/manifest.json must correspond to something real.

The manifest declares `policyClasses`, `auditEvents` and `dangerousSurfaces`. Nothing
read the file, and — checked for the first time here — not one of the declarations
appears anywhere in the source. The audit events it promises are emitted by no code;
the dangerous surfaces it names are referenced by no gate. A reader encountering the
manifest would reasonably conclude this component emits those events. It emits none.

Making that fail outright would put the build red on arrival, which is how a check
gets disabled rather than satisfied. So this is a RATCHET: the existing gap is named,
dated and counted where it cannot be overlooked, and any declaration added from now on
must be real or the build fails. Debt shrinks or holds; it does not grow quietly.

Remove entries from KNOWN_UNIMPLEMENTED as each is genuinely emitted. When the set is
empty, delete it and the check becomes a plain assertion.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".sourceos" / "manifest.json"

# Declared 2026-07-29 but emitted/gated by nothing. Each is a promise the manifest
# makes on this component's behalf that the component does not currently keep.
KNOWN_UNIMPLEMENTED = {
    "boot.rollback.planned",
    "boot.rollback.applied",
    "boot.rollback.denied",
    "boot.chain.validated",
    "boot.efi_vars_mutable",
    "boot.rollback.forced",
    "boot.generation.deletion",
}

CHECKED_KEYS = ("auditEvents", "dangerousSurfaces")


def source_blob() -> str:
    """Every tracked file except the manifest itself — a declaration cannot satisfy
    itself by being declared."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    parts = []
    for rel in files:
        if rel == ".sourceos/manifest.json":
            continue
        try:
            parts.append((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(parts)


def main() -> int:
    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST.relative_to(ROOT)}; nothing to check")
        return 0

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    blob = source_blob()

    missing: list[str] = []
    debt: list[str] = []
    found: list[str] = []

    for key in CHECKED_KEYS:
        for item in manifest.get(key, []):
            if item in blob:
                found.append(f"{key}:{item}")
            elif item in KNOWN_UNIMPLEMENTED:
                debt.append(f"{key}:{item}")
            else:
                missing.append(f"{key}:{item}")

    for item in sorted(found):
        print(f"  IMPLEMENTED  {item}")
    for item in sorted(debt):
        print(f"  DEBT         {item}  (declared, emitted by nothing)")

    # An allowlist entry that no longer appears in the manifest is stale bookkeeping:
    # it would silently permit re-adding that declaration unimplemented later.
    declared = {i for key in CHECKED_KEYS for i in manifest.get(key, [])}
    stale = sorted(KNOWN_UNIMPLEMENTED - declared)
    for item in stale:
        missing.append(f"KNOWN_UNIMPLEMENTED lists {item!r}, which the manifest no longer declares")

    if missing:
        print(f"\n{len(missing)} undeclared-but-unimplemented item(s):\n", file=sys.stderr)
        for item in missing:
            print(f"  {item}", file=sys.stderr)
        print(
            "\nA manifest declaration must correspond to something in the source. "
            "Implement it, or — only if it is genuinely deferred — add it to "
            "KNOWN_UNIMPLEMENTED with a dated reason.",
            file=sys.stderr,
        )
        return 1

    if debt:
        print(f"\nOK ratchet holds — {len(found)} implemented, {len(debt)} known debt, 0 new")
    else:
        print(f"\nOK every manifest declaration corresponds to source ({len(found)} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
