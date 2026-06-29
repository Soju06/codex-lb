# Proposal: Harden HTTP bridge hard-affinity 1011 retries

## Problem

Codex-compatible clients can use the HTTP/SSE Responses route. That route uses the HTTP responses bridge, which opens an upstream WebSocket on behalf of the client.

When the upstream WebSocket closes with code `1011` before `response.completed`, the bridge currently treats the pending pre-created request as replayable and skips retrying on the same account. For hard continuity keys such as `session_header`, that can re-send the same logical session on another account and produce repeated `stream_incomplete` failures.

The same bridge path can also forward downstream HTTP/SSE request headers into the upstream WebSocket connection. In local reproduction this produced upstream WebSocket requests carrying HTTP-only headers and an incompatible `OpenAI-Beta: responses=experimental` token, which caused repeated upstream closes before `response.created`.

## Change

- Preserve account ownership for hard HTTP bridge keys during pre-created replay after upstream close `1011`.
- Continue allowing soft-affinity bridge sessions to skip the failed account for `1011`.
- Filter HTTP-only and hop-by-hop headers before HTTP bridge create/reconnect opens the upstream responses WebSocket.
- Normalize upstream responses WebSocket beta headers by removing HTTP Responses beta tokens and preserving `responses_websockets=2026-02-06`.
- Add regression coverage for hard `session_header` replay, HTTP bridge header filtering, and WebSocket beta normalization.

## Impact

Hard continuity retries remain bounded to the owning account. If that account cannot complete the replay, the request fails with the existing retryable stream error instead of fanning out across unrelated accounts.

HTTP bridge upstream WebSocket connections use the same WebSocket-safe header shape as direct Codex WebSocket connections, while preserving continuity headers required for affinity.
