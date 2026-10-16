## Context

See proposal.md for motivation. Existing terminal settlement already parses
reset fields, but retry branches rebuild a message-only error and mark health
handled before terminal settlement can restore them. Deferred keyed health
must continue to settle reservations before writing account health.

## Goals / Non-Goals

Preserve the same upstream health evidence across retry and terminal paths.
Do not migrate account-scoped context, change client-visible envelopes, relax
file pins, alter retry budgets, or change Codex approval policy or credentials.

## Decisions

- Restore the shared frame-to-health conversion from closed PR #2440. Reuse
  existing event extraction, OpenAI parsing, and typed health conversion so
  flattened errors and response.failed errors retain equivalent semantics.
- Pass the converted error through every affected HTTP bridge and direct
  WebSocket retry branch, including accepted output-free replay. Keep deferred
  health ownership and ordering unchanged.
- Ignore JSON booleans in numeric parsing. Python treats bool as int, while
  reset fields are numeric deadlines or durations; malformed boolean metadata
  must retain the established metadata-free fallback.
- Validate through the real HTTP routes and account persistence, then through
  unit tests covering error shapes, file/client ownership, and keyed settlement.

## Risks / Trade-offs

- A later eligible request may retire a genuinely exhausted owner sooner.
  Existing retirement predicates and replay/ownership gates remain authoritative.
- Shared numeric parsing also serves client envelopes. Valid numeric strings
  remain accepted; malformed metadata tests cover compatibility.
- A transient owner backoff or a reviewer conversation with insufficient
  history can still fail. This change does not claim to fix those paths.

## Migration Plan

No schema or setting changes. Deploy and roll back as an ordinary code update.
Do not modify or restart the investigated production container for validation.
