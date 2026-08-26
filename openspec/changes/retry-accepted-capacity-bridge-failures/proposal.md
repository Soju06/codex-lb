# Retry Accepted Capacity Bridge Failures

## Why

The bounded pre-visible retry path only covers failures that arrive *before*
upstream accepts a response. `_websocket_precreated_retry_error_code` returns
`None` as soon as `response_id` is set or `response_event_count` is above zero,
and it refuses a capacity error that carries a response id at all.

Upstream does not only refuse before acceptance. It also accepts a request,
emits `response.created` (and often `response.in_progress`), and *then*
terminates the response with `overloaded_error` / `server_is_overloaded` /
"selected model is at capacity" having produced no model output. That failure
is as replay-safe as the pre-created one — nothing has been generated and
nothing output-bearing has reached the client — but the classifier is
structurally unable to see it, so it lands on the client as terminal and the
agent stops mid-task.

The same shape reaches the HTTP bridge as an abrupt upstream close after
lifecycle-only events, and the direct Responses paths have two adjacent gaps:
a non-streaming overload envelope whose HTTP status is not 500 returns without
entering the transient retry path, and an initial upstream stream EOF is
surfaced instead of retried.

## What Changes

- Classify one accepted-but-output-free capacity failure on the native Codex
  HTTP bridge as replayable, for both a terminal error event and an abrupt
  transport close, and replay it once on the same account after the existing
  transient backoff.
- Keep the replay invisible to the client: the accepted response's ID is
  carried forward, the replay's own `response.created` is suppressed, and
  later events are rewritten to the ID the client is already reading.
- Bound the replay by downstream attachment, request deadline, replay budget,
  pending-request count, and every existing visible-output signal; a replay
  that never reaches the wire restores the request exactly as the pre-created
  retry circuit expects to find it.
- Route direct non-streaming Responses overload envelopes and initial
  output-free stream EOFs through the existing bounded transient retry path,
  and keep the real upstream error when that budget is exhausted.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: extend upstream-overload retry coverage from
  pre-created failures to accepted output-free failures on the native Codex
  bridge, and to the direct non-streaming and initial-EOF stream paths.

## Impact

- HTTP bridge classification and replay
  (`app/modules/proxy/_service/http_bridge/upstream_events.py`,
  `app/modules/proxy/_service/http_bridge/request_submit.py`,
  `app/modules/proxy/_service/http_bridge/protocol.py`,
  `app/modules/proxy/_service/http_bridge/helpers.py`), streaming retry
  (`app/modules/proxy/_service/streaming/retry.py`,
  `app/modules/proxy/_service/streaming/mixin.py`), and output bookkeeping
  (`app/modules/proxy/_service/support.py`).
- No API, schema, migration, dependency, configuration, or dashboard changes.
  The replay reuses the existing transient backoff, replay counter, and
  request budget; it adds no setting and no new loop.
