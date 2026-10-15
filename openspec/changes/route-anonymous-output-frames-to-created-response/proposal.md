# Change: route-anonymous-output-frames-to-created-response

## Why

Two independent Responses requests that share one HTTP bridge session (the same soft `prompt_cache` lane: an explicit `prompt_cache_key` without a session header, a shared `conversation`, or a derived key on older releases) are pipelined on one upstream WebSocket. The session's response-create gate releases on `response.created`, so the second request sends its `response.create` while the first response is still streaming. Upstream item frames (`response.output_item.added`, `response.output_text.delta`, reasoning deltas, ...) carry no response id. `_match_websocket_request_state_for_anonymous_event` gives every such frame to the single pending request that has no response id yet, a rule written for anonymous *error* frames that reject a not-yet-created request. For output frames that is the wrong owner: the sibling still waiting for its own `response.created` receives the first response's output, its public normalizer buffers 64 pre-created frames and then synthesizes `response.failed upstream_stream_truncated`, and the first request ends with a successful `response.completed` whose `output` is `[]` because none of its `output_item` events reached it (Soju06/codex-lb#2350). The same misroute contaminates a visible sibling with the output of a cancelled-but-draining request whose response upstream already created.

## What Changes

- `_match_websocket_request_state_for_anonymous_event` receives the frame's `event_type` from the HTTP bridge reader, the direct WebSocket reader, and direct WebSocket archive attribution. For an anonymous `response.*` frame that is not `response.completed`, `response.failed` or `response.incomplete` (response output) the matcher first returns the single pending request that already has a response id, whether that request is visible or draining. Anonymous `error`, `response.completed`, `response.failed` and `response.incomplete` frames and vendor telemetry frames such as `codex.rate_limits` keep the existing pre-created ownership rules, and every other branch (previous-response continuity errors, draining preferences, single-request sessions) is unchanged.
- When no pending request, or more than one, already has a response id, the pre-existing matching rules apply unchanged. Two visible created requests with nothing else pending still leave the frame unmatched (unmatched upstream liveness); arrangements with a further waiting or draining request keep the base branch's visible/draining/unresolved preferences. Fail-closed handling for every overlapping-created arrangement would be a separate behaviour decision and is not part of this change.
- No new setting, no schema change, no change to lane forking or gate release.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: ADDED requirement "Anonymous upstream output frames belong to the created response".

## Impact

- Code: `app/modules/proxy/_service/websocket/helpers.py` (one keyword and one guarded block in the matcher), `app/modules/proxy/_service/http_bridge/upstream_events.py` and `app/modules/proxy/_service/websocket/mixin.py` (pass the frame kind at the anonymous-match call).
- Behaviour: only when at least two requests are pending on one upstream socket and exactly one already has a response id. The pipelined sibling now receives nothing until its own `response.created` and then a complete response instead of `upstream_stream_truncated`; the first request receives its own output. Conversation archive attribution of those frames follows the same owner.
- Operators: no action.
