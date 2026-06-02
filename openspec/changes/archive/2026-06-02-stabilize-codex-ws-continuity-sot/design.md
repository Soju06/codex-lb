# Design: Codex WebSocket long-wait continuity

## Problem model

Observed failure shape:

1. Codex CLI sends `response.create` over `wss://.../backend-api/codex/responses`.
2. The model emits a tool call, e.g. background terminal wait.
3. The client waits for a long-running command.
4. Upstream ChatGPT invalidates the previous Responses anchor while the local Codex turn is still semantically active.
5. When the tool returns, Codex sends a follow-up `response.create` with `previous_response_id` plus tool output / delta input.
6. Same account/session routing can still receive upstream `previous_response_not_found`.

The key insight is that the upstream anchor is not durable. However, the minimal recovery point is probably the Codex client, not a large proxy-owned conversation ledger.

## Official Codex client behavior

Current upstream Codex client behavior, verified against `openai/codex`:

- `prepare_websocket_request` builds incremental WebSocket requests by comparing the new full conversation-history request against the previous request plus the previous response output items. If the prefix matches, it sends only the delta plus `previous_response_id`.
- `response.failed` becomes an `ApiError` in `process_responses_event`.
- `run_turn` treats that `ApiError` as a hard `EventMsg::Error` and ends the current turn.
- After that failure, the next user prompt is sent as a full `response.create` without `previous_response_id`, because the previous `last_response` has been consumed/cleared. This matches the observed behavior: typing any text after the hard error restarts with full session context and works.

Therefore, the direct minimal fix is to make that same full-create reset happen automatically inside the failed turn:

1. Detect a stale-anchor continuity error (`previous_response_not_found` or codex-lb's sanitized equivalent) as recoverable.
2. Reset/clear the incremental WebSocket session state for the current `ModelClientSession`.
3. Rebuild the sampling request from conversation history.
4. Retry once as a full `response.create` without `previous_response_id`.
5. Do not surface a hard turn-ending error unless that retry fails.

This is the "soft error" behavior Soju previously identified in the Codex client analysis.

## Primary invariant

For Codex-native WebSocket long-wait continuity, the active invariant is:

> A stale upstream `previous_response_id` during a tool-output continuation must not hard-end the turn before Codex has attempted one full-context retry without `previous_response_id`.

This is a client/session retry invariant. The proxy's primary responsibility is to expose the failure in a shape the client can classify without leaking raw upstream internals.

## Proxy responsibilities

codex-lb should still enforce these narrower proxy-side invariants:

- Never leak raw `previous_response_not_found` or the missing `resp_...` id downstream.
- Preserve enough error identity for Codex to classify the event as stale-anchor continuity loss. If `stream_incomplete` is too generic for the client, add a sanitized stable code/detail such as `codex_previous_response_stale` without exposing the upstream response id.
- Do not convert stale-anchor errors into unrelated hard failures such as owner unavailable, quota, or generic invalid request.
- Keep direct `/backend-api/codex/responses` WebSocket tests separate from HTTP bridge tests.

## Server-side replay fallback

A proxy-owned local replay ledger can still be useful for clients we cannot change, but it is not the primary canonical fix when the Codex client is patchable.

If implemented, keep it narrow:

- only one replay attempt;
- only before downstream-visible output for the failed follow-up;
- only when the proxy has verified self-contained replay state;
- no broad DB-backed conversation reconstruction until a client-side fix is ruled out.

Do not make the proxy a second full conversation-state owner by default.

## Source-of-truth cleanup

This change is the active SoT for this failure mode. Existing older changes remain historical unless their requirements are explicitly copied here. Do not add another branch-local previous-response patch without updating this change first.

Implementation should now be split into two tracks:

1. **Client soft-reset retry track** — preferred if we can patch the Codex client/fork used by Soju.
2. **codex-lb compatibility track** — sanitize/classify errors so the client can trigger the soft reset; optionally keep narrow proxy replay only as fallback.
