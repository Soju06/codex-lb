# Stabilize Codex WebSocket continuity and source of truth

## Why

Codex CLI sessions that run long background terminal commands can sit idle long enough for the upstream ChatGPT Responses `previous_response_id` anchor to expire. When the terminal eventually returns, Codex sends a follow-up `response.create` that is usually a delta around the previous response, often tool output tied to the prior model tool call. Re-sending that delta with the same `previous_response_id` is not recovery; it repeats the failing dependency on an expired upstream anchor.

Official Codex client behavior shows the minimal recovery shape: after a WebSocket stream error, the current turn hard-ends, but the next user prompt is sent as a full `response.create` without `previous_response_id` and the session continues. The failure is that this reset happens only after user intervention instead of as a soft automatic retry inside the same turn.

Prior fixes treated `previous_response_not_found` as a path-specific routing, masking, or proxy replay bug. Those patches were necessary but insufficient because the source-of-truth invariant was missing: stale upstream anchors need a full-context retry/reset path, and raw upstream errors must not hard-end the user turn before that retry is attempted.

The current repository also has too many branch-level / change-level partial fixes around this problem. This change becomes the active source of truth for Codex WebSocket long-wait continuity, superseding one-off previous-response masking/retry changes for this failure mode.

## What Changes

- Define upstream `previous_response_id` as an ephemeral anchor, not durable state.
- Prefer the Codex client soft-reset fix: classify stale-anchor continuity loss, reset incremental WebSocket session state, rebuild from conversation history, and retry once without `previous_response_id` before surfacing an error.
- Keep codex-lb focused on sanitized/classifiable error semantics for direct `/backend-api/codex/responses` WebSocket traffic.
- Do not leak raw `previous_response_not_found` or missing response ids downstream.
- Treat proxy-side anchorless replay ledgers as optional compatibility fallback for clients that cannot be patched, not the primary design.
- Consolidate the source of truth: OpenSpec delta + context docs + focused regression tests for long background-terminal waits on the Codex-native direct WebSocket path.

## Impact

Long-running background terminal waits become recoverable by automatically triggering the same full-session restart behavior that currently works only after the user types another message. codex-lb remains responsible for safe masking/classification, while the client owns the turn-level retry semantics.
