# Agent Operating Instructions

Work issue-first.

Rules:
- One repo, one issue, one PR.
- Inspect the live repository before editing.
- Keep scope bounded to the issue body.
- Do not broaden scope without asking in the issue.
- Do not touch unrelated files.
- Do not claim production readiness unless acceptance criteria prove it.
- Include validation evidence in the PR body.
- Leave known gaps explicit.

PR body must include:
- What changed.
- Exact commands run.
- Pass/fail output summary.
- Known gaps.
- Anything blocked.

Never:
- Commit secrets, tokens, credentials, private keys, or host-specific boot secrets.
- Invent release URLs, checksums, SBOMs, or provenance.
- Claim boot, installer, recovery, rollback, or production readiness from documentation-only work.

SourceOS boot-specific rules:
- This repo consumes NLBoot and SourceOS specs; do not duplicate canonical schemas.
- Keep implementation-facing boot/recovery integration separate from schema ownership.
- M2 is first-class proof hardware, not the only target.
- Documentation and fixtures come before host-changing behavior.
- Real boot-entry, disk, installer, or rollback behavior requires explicit review.

Validation:
- Use repository-native validation commands if present.
- If adding JSON fixtures, add or update Makefile validation.
