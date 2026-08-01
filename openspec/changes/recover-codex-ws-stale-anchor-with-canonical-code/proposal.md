# Recover Codex WebSocket stale anchors with the canonical error code

## Summary

On the Codex-native `/backend-api/codex/responses` WebSocket route, when an ephemeral `previous_response_id` anchor goes stale, codex-lb emits a nonstandard terminal `response.failed` classifier (`codex_previous_response_stale`). No unmodified Codex client recognizes that code, so the turn ends and only a client restart recovers. Change the sanitized signal on this route to the canonical `previous_response_not_found` code, with the raw upstream envelope and the missing `resp_...` id stripped, because that is the code unmodified clients already act on to retry once with full context.

## Why

The requirement `Codex WebSocket stale-anchor failures remain recoverable by a full-context retry` already intends the client to recover on a stable classifier. But standard Codex clients recover by matching the canonical error *code*, not by reading a message: the reference pi / pi-ai transport is confirmed from source to retry only on `error.code == "previous_response_not_found"` and to ignore any other code. The official Codex client's recovery on the same canonical code is confirmed from its own source, not just assumed by analogy (see `design.md`'s Load-bearing assumption). Emitting a proxy-specific code silently disables that built-in recovery, turning a recoverable continuity loss into a turn-ending error that needs a manual restart. A client cannot be expected to learn a proxy-specific code, so the fix belongs on the surface that deviated from the canonical contract.

This route already recovers transparently, without any client involvement, when the client's own payload happens to be a self-contained full resend (see `design.md`'s Context). The rename bug only reaches the client in the residual case that mechanism cannot cover — a delta-only continuation, exactly the shape pi/pi-ai reported — where codex-lb has no independently-reconstructable history to replay with and a client-actionable signal is the only remaining option.

Reference: pi report [#1529](https://github.com/Soju06/codex-lb/issues/1529).

## What Changes

- On the Codex-native WebSocket route, stale-anchor continuity failures are surfaced with `error.code = "previous_response_not_found"`, sanitized to remove the raw upstream error envelope and the missing (stale) `previous_response_id`. This applies to both the mid-stream `response.failed` shape and the top-level wrapped `"type": "error"` shape used for connect-time failures (`_wrapped_websocket_error_event`); the latter had its own independent re-masking step that would otherwise have silently reverted the sanitized code back to `stream_incomplete` — see `design.md`'s Implementation guidance. On the `response.failed` shape, the current downstream response id is preserved for event correlation, as before; the top-level `"type": "error"` shape carries no response id field at all, sanitized or not, so there is nothing to preserve there.
- The nonstandard `codex_previous_response_stale` classifier is no longer used on this route.
- Public `/v1/responses` WebSocket clients keep the existing `stream_incomplete` masking; OpenAI-compatible clients do not expect the Codex continuity code.
- Sibling requirements that currently assert `stream_incomplete` masking for the Codex-native WebSocket route (`Codex WebSocket top-level previous-response errors are masked`, `Codex WebSocket wrapped errors follow official client shape`) are reconciled so their Codex-native scenarios use the canonical `previous_response_not_found` signal while their public `/v1` scenarios keep `stream_incomplete`. This delta modifies the two authoritative requirements; the owner drives the dependent wording per the centralized-continuity requirement.
- Add WebSocket-surface regression coverage asserting the client-visible code is `previous_response_not_found` with the stale `previous_response_id` and raw upstream envelope absent (the current response id may remain).

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `responses-api-compat`: Redefines the sanitized signal for Codex-native WebSocket stale-anchor failures as the canonical `previous_response_not_found` code, and scopes the "never leak raw upstream errors" masking to the raw envelope and the missing response id rather than to the bare code.

## Non-Goals

- No change to `upstream_unavailable` (owner-account-unavailable) or suppressed-duplicate `stream_incomplete` signaling; those share the same delivery problem but are a follow-up.
- No change to the HTTP bridge path, which is already client-recoverable.
- No change to public `/v1/responses` masking (`stream_incomplete` is retained).
- No client-side change; the fix targets the deviating proxy surface only.
- No change to account selection, routing, retry decisions, or upstream payload shape.
