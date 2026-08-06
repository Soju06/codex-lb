## Context

Codex stores a session provider tag both in the first `session_meta` record of its JSONL segments and in `state_*.sqlite` thread rows. A provider transition interrupted by a Codex update can leave only one of those stores retagged. The existing retag command is deliberately whole-home and is therefore inappropriate for repairing a single inconsistent session.

## Goals / Non-Goals

**Goals:**

- Detect only session IDs whose JSONL and SQLite provider tags disagree with the currently active `openai` or `codex-lb` provider.
- Preview the exact IDs and metadata components that would change.
- Apply a confirmed repair through the existing targeted backup, quiescence, atomic replacement, and verification mechanisms.
- Expose machine-readable CLI results for external supervisors without promising a specific UI integration.

**Non-Goals:**

- Do not infer that a session consistently tagged for another provider is missing.
- Do not change `config.toml`, switch profiles, create SQLite rows, or run a global retag.
- Do not expose raw transcript contents in the UI.

## Decisions

1. **The Python metadata module owns detection and mutation.** It already owns the bounded JSONL metadata reader, SQLite access, backup, and verification behavior. A new session-ID-scoped command prevents the Windows UI from duplicating file-format logic.

2. **The active provider is the repair target.** A session is eligible only when its discovered JSONL/SQLite provider set contains the active provider and exactly one opposite supported provider. This repairs partial transitions while excluding sessions that are consistently owned by another profile or have unsupported metadata.

3. **Preview and apply use one frozen candidate list.** The preview returns session IDs, source provider, affected JSONL segment count, and affected SQLite row count. Apply accepts only those IDs, revalidates their eligibility, and writes no non-candidate session.

4. **Targeted repair reuses the retag progress protocol.** Backup and JSONL rewrite byte progress plus SQLite update and verification item progress use the existing `CODEX_LB_RETAG_PROGRESS` stream. JSON result mode sends progress to stderr so stdout remains one parseable result document.

5. **The CLI exposes a consumer-neutral workflow.** Preview and repair are separate subcommands with optional JSON results. The repair command requires explicit session IDs and confirmation. External supervisors remain responsible for process quiescence, presentation, and deciding whether to invoke repair.

## Risks / Trade-offs

- [Codex writes while repair is running] → The CLI warns that Codex must be closed and requires explicit confirmation; external supervisors that automate the command must enforce process quiescence.
- [Stale preview] → Apply revalidates every candidate and refuses a changed or no-longer-eligible ID.
- [Consistently old session is misclassified] → Eligibility requires disagreement, not merely a provider different from the active profile.
- [Large session homes] → Detection reads only bounded first metadata records and queries SQLite once per state DB.

## Migration Plan

1. Ship the session-ID-scoped CLI preview/apply interface with targeted unit and CLI coverage.
2. Document the JSON and explicit-confirmation contract for external supervisors.
3. Existing configuration, session data, and profile-transition behavior require no migration. A failed apply retains its targeted backup for restoration.
