# Tasks

- [x] Accept compact context and tool-search call/output pairs in account-neutral replay safety checks.
- [x] Preserve completed compaction items in projected fresh replay payloads without retaining response-owned ids.
- [x] Keep session-level trim safety from overriding the original replay-safety decision unless a durable full-resend proof exists.
- [x] Allow account-neutral bridge recovery to select another eligible account after the silent owner is excluded.
- [x] Keep explicit required-owner continuity failures fail-closed.
- [x] Pass response-event evidence into stale response-create gate retirement.
- [x] Add replay-safety and HTTP bridge regression coverage for post-compact recovery.
