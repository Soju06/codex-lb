# Retry injected websocket stale anchors

## Summary

When codex-lb injects a WebSocket `previous_response_id` session anchor into a full-context Codex turn, a stale-anchor upstream error must use the prepared fresh replay before any owner-unavailable fail-closed handling. Codex WebSocket prewarm completions must also be classified separately so empty prewarm frames do not masquerade as normal user-turn progress. Codex auto-compaction requests must be bounded by the proxy compact budget so a hung compact endpoint cannot wedge a thread indefinitely.

## Why

Live Codex sessions can wait on long tool output and later send full context while codex-lb trims that context behind a session anchor. If the upstream loses that anchor, retrying or failing under the preferred owner keeps the session stuck even though codex-lb already has a safe no-anchor replay body. Separately, empty-output prewarm completions can make request logs and account health look successful even when no user-visible turn progressed. Auto-compaction is another live wedge path: if upstream accepts the compact HTTP request but never completes it, Codex keeps retrying compaction and no final answer is emitted.

## Scope

- Direct WebSocket `/backend-api/codex/responses` and `/v1/responses` continuity handling.
- Request-log and account-health classification for direct WebSocket Codex prewarm requests.
- Codex compact request timeout/logging behavior for `/backend-api/codex/responses/compact`.
- No change to short previous-response continuations that lack a retry-safe full-context body.
