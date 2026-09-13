## Context

Hard turn-state requests can omit `previous_response_id` while still carrying a
real Codex turn-state continuity anchor. Their replay identity is protected by
the durable operation ledger, but the startup cooldown guard runs before
operation registration. It therefore classifies the request as continuity-bound
without safe replay and returns 503 before the ledger can serialize recovery.

The HTTP response already includes `Retry-After`, and an already-started SSE
failure includes an SSE `retry:` directive. Production telemetry shows Codex
Desktop retrying in milliseconds anyway, so another client hint does not address
the observed failure mode.

## Decision

Treat a turn-state-only hard request as eligible to wait through cooldown only
when all of the following hold:

- recovery mode is `server_anchored_replay_once` or
  `server_indefinite_recovery`;
- the durable operation ledger is enabled;
- the request has a real hard continuity anchor;
- the bridge has both a durable session id and current owner epoch;
- no response id or upstream response event has been observed; and
- request budget remains.

The wait is clamped to the smaller of cooldown remaining and request budget.
It does not reserve a replay, mutate the operation journal, or send upstream.
When the cooldown expires, normal submission performs the existing operation
fingerprint lookup and atomic recovery claim. One-shot mode keeps its existing
maximum of one recovery dispatch; indefinite mode retains its existing explicit
opt-in semantics.

## Explicit exclusions

- No change to the default `fail_closed` mode.
- No transparent replay without a durable session and owner fence.
- No cross-account, file-pinned, image, soft-affinity, or eventful recovery.
- No weakening of operation fingerprint, ownership, or replay-count checks.
- No infinite retry added by this change; bounded one-shot mode is the
  recommended deployment setting for this incident class.
