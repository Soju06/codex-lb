# Change: model-scoped-rejection-failover

## Why

Two upstream rejections are about the serving account, not the request, and
the proxy currently lets either one end a movable request on the first
account that carried it.

An upstream `model_not_found` rejection is about the requested model, not the
health of the account that carried it. Treating it as a transient account
failure lets one unavailable model back off every candidate account and block
unrelated traffic. Conversely, a movable request can legitimately find an
account with a different entitlement before it has accepted an upstream
response. The WebSocket path must make that same distinction for connect and
pre-created terminal events without violating continuity ownership.

A fresh, self-contained HTTP Responses request can receive the upstream
`server_is_overloaded` or `overloaded_error` terminal as either its first SSE
event or after only `response.created` / `response.in_progress`. The direct
HTTP stream forwards those lifecycle frames and the terminal before its retry
owner can classify the failure, so the request is downstream-visible and the
already-enabled deterministic failover policy cannot exclude the rejected
account. A soft prompt-cache affinity then sends several independent fresh
requests to that same account until the separate three-rejection overload
backoff changes later selection.

## What Changes

- Classify the exact upstream `model_not_found` code as model-scoped for both
  bounded pre-visible failover and account-health neutrality.
- Let a movable pre-created Responses WebSocket request retry that exact error
  before `response.created`; preserve the existing `invalid_request_error`
  behavior for all other ordinary invalid requests.
- Surface the original rejection for a required WebSocket owner; it must not
  exclude that owner and replace the error with an owner-unavailable result.
- Hold only the accepted lifecycle prelude of a replay-safe fresh HTTP stream
  until the first output or terminal event, and route an output-free
  `server_is_overloaded` or `overloaded_error` terminal back to the existing
  bounded account-exclusion path while deterministic failover is enabled.
- Keep that exception unavailable to a request with a previous-response,
  turn-state, file, single-account, dispatched-payload, or other hard owner
  (including a raw legacy `CODEX_SESSION` row sticky selection may resolve for
  a thread-scoped request), to the single replacement selected after an
  account/model rejection, and after content, tool output, or terminal output
  evidence. Reuse the existing account exclusion, lease release, health
  settlement, and `overload_backoff` paths; disabling deterministic failover
  remains fail-closed.

## Capabilities

### Modified Capabilities

- account-routing: model-scoped rejections do not alter account health.
- responses-api-compat: pre-created WebSocket model rejection is retryable
  only for movable requests and only before acceptance.
- proxy-admission-control: a fresh output-free HTTP overload probes one
  alternate account before the client sees any response lifecycle.

## Impact

One shared proxy classification, the existing stream health funnel, the
existing Responses WebSocket connect and pre-created replay paths, and the
direct HTTP SSE boundary in `app/modules/proxy/_service/streaming/mixin.py`
with its retry owner in `app/modules/proxy/_service/streaming/retry.py`. No
setting, schema, dashboard, caller retry loop, timeout, or transport protocol
is added or changed.
