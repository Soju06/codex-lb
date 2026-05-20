## Context

Live compaction checks showed persisted compaction events while the proxy request log still had `context_length_exceeded` rows. Operators need a cheap view that separates "Codex compacted" from "the proxy still saw a context-window failure" without exposing raw conversation or session identifiers.

## Decisions

- Keep compaction protocol behavior in Codex; this change only observes proxy request-log failures.
- Lower the host auto-compaction threshold to 750000 tokens for new Codex sessions.
- Extend `scripts/codex_lb_live_snapshot.py` using persisted `request_logs` data so the existing live snapshot remains the operator entry point.
- Report bounded aggregates plus the newest matching rows, with raw `session_id` replaced by a short SHA-256 hash.
- Support both SQLite unit fixtures and the PostgreSQL-backed live service query shape.

## Risks

- Old sessions may keep their prior auto-compaction threshold until restarted.
- Context-window failures can still appear when an upstream request reaches the model before the client-side compaction trigger fires.
- The live snapshot query must avoid leaking raw identifiers while leaving enough shape to correlate repeated failures.
