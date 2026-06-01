# Continuity source-of-truth cleanup note

This note records why `stabilize-codex-ws-continuity-sot` is the active source of truth for the long background-terminal `previous_response_id` failure mode.

## Branch audit snapshot

A local branch scan found 36 branch refs whose names mention Codex, Responses, WebSocket, bridge, continuity, terminal, or previous-response behavior. Notable overlapping branches include:

- `komzpa/codex/mask-single-previous-response-miss`
- `komzpa/fix/durable-http-bridge-full-resend-trim`
- `komzpa/fix/partial-previous-response-stream-errors`
- `komzpa/fix/responses-continuity-and-large-bodies`
- `komzpa/fix/stream-incomplete-long-codex-turns`
- `komzpa/fix/websocket-codex-full-replay-trim`
- `komzpa/fix/websocket-created-drop-replay`
- `komzpa/fix/websocket-prev-response-sanitize`
- `komzpa/fix/websocket-stream-error-statuses`
- `komzpa/live/previous-response-dns-plan-plus-prs`
- `komzpa/split/555-continuity-core`
- `komzpa/split/555-ws-prev-trim`

These names show the previous work was split across masking, trimming, full replay, partial stream errors, and continuity core branches. That split is exactly the problem: no single normative requirement explained how long-wait tool-output continuations recover when upstream invalidates an otherwise correctly routed anchor.

## Active source of truth

The active source of truth is now:

- `openspec/changes/stabilize-codex-ws-continuity-sot/proposal.md`
- `openspec/changes/stabilize-codex-ws-continuity-sot/design.md`
- `openspec/changes/stabilize-codex-ws-continuity-sot/tasks.md`
- `openspec/changes/stabilize-codex-ws-continuity-sot/specs/responses-api-compat/spec.md`

## Superseded framing

This change supersedes the following framings for the long-wait failure mode:

- “Just mask the raw upstream error.” Masking is required but not recovery.
- “Just retry the client request.” Retrying the same anchor-dependent delta repeats the failure.
- “Just route to the owner account/session.” Correct routing is necessary but insufficient; the observed case failed on the same account/session.
- “Just keep the WebSocket alive.” A live socket does not make the upstream Responses anchor durable.
- “Fix each WebSocket envelope shape independently.” Envelope parsing is necessary, but the invariant must be path-wide.

## Retained requirements from older work

Older work remains useful where it provides:

- raw-error masking and response-id redaction;
- direct `/backend-api/codex/responses` WebSocket tests;
- replay trimming / size-guard helpers;
- stream-incomplete fail-closed behavior;
- HTTP bridge learnings, as long as they are not mistaken for direct WebSocket coverage.

Those requirements should be copied into this change as tests or helper behavior when implementation starts. Do not create another previous-response branch without updating this SoT first.

## Implemented codex-lb contract

This change now implements the codex-lb side of the contract for direct
`/backend-api/codex/responses` WebSockets:

- stale upstream `previous_response_id` errors are still fail-closed and never
  leak raw `previous_response_not_found` or the missing upstream `resp_...` id;
- Codex-native direct WebSocket clients receive the sanitized classifier
  `codex_previous_response_stale` with the retry hint "retry without
  previous_response_id";
- OpenAI-compatible `/v1/responses` WebSocket clients keep the older generic
  `stream_incomplete` masking so this Codex-only classifier does not become a
  public OpenAI-compatible error code;
- proxy-side conversation replay remains out of the primary path. Compatible
  clients should use the classifier to soft-reset incremental WebSocket state
  and retry once from full conversation history.
