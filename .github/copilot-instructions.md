Use the GitHub issue body as the source of truth.

Before editing:
1. Read the issue.
2. Inspect the repository.
3. Identify existing validation commands.
4. Keep the PR bounded.

When implementing:
- Prefer existing repository patterns.
- Add fixtures and validators with documentation/spec changes when applicable.
- Do not duplicate canonical schemas from `SourceOS-Linux/sourceos-spec`.
- Do not implement real boot, installer, recovery, rollback, or host-changing behavior unless the issue explicitly requests it.
- Keep M2 support first-class but preserve generic platform paths.

When opening the PR:
- Link the issue.
- Include validation evidence.
- List known gaps.
- State non-goals preserved.
- Do not mark ready if validation did not run.
