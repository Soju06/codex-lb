# responses-api-compat Delta

## ADDED Requirements

### Requirement: Pre-response-start bridge silence has its own classification

The HTTP responses session bridge MUST NOT report a failure that occurs before
the request's first response event as `stream_idle_timeout`. Every terminal the
bridge produces while `response.created` has not been observed and no response
event has been counted MUST use the distinct code `bridge_eventless_timeout`.

That classification MUST appear in the bridge's own log line, in the durable
request-log failure metadata (`failure_detail` = `bridge_eventless_timeout`,
`failure_phase` = `bridge`), in the HTTP bridge retry-circuit `last_detail`, and
in the client-visible error payload. The retry-circuit detail MUST NOT be
aliased onto `stream_idle_timeout`.

The client-visible error MUST remain retryable: HTTP status `503` and a message
that states no response was created upstream and the request is safe to repeat.
The message MUST NOT attribute the failure to the upstream.

`stream_idle_timeout` remains the classification for a stream that produced at
least one response event and then went silent for `stream_idle_timeout_seconds`.

#### Scenario: Pre-response silence is reported as an eventless bridge timeout

- **GIVEN** an HTTP bridge request whose downstream event queue has produced no
  response events
- **WHEN** the pre-response silence budget expires
- **THEN** the emitted `response.failed` event carries code
  `bridge_eventless_timeout`
- **AND** the request log records `failure_detail=bridge_eventless_timeout` and
  `failure_phase=bridge`
- **AND** the hard-affinity retry circuit records `last_detail` of
  `bridge_eventless_timeout`
- **AND** the error message does not mention the upstream
- **AND** the equivalent HTTP error status is `503`

#### Scenario: Post-response idle keeps the stream idle classification

- **GIVEN** an HTTP bridge request that already received a response event
- **WHEN** the stream stays silent past `stream_idle_timeout_seconds`
- **THEN** the emitted `response.failed` event carries code
  `stream_idle_timeout`
- **AND** no `bridge_eventless_timeout` request-log detail is recorded

### Requirement: The pre-response silence budget is settings-derived

The pre-response silence budget MUST be a named quantity derived from
configuration, not the implicit product of `_STREAM_KEEPALIVE_MAX_COUNT` and
`sse_keepalive_interval_seconds`.

The budget MUST be the minimum of
`http_responses_session_bridge_stuck_gate_retire_after_seconds`,
`stream_idle_timeout_seconds`, and
`http_responses_session_bridge_request_budget_seconds`, so that the downstream
pre-response watchdog can never outlive the owner-side stuck gate, the
configured idle budget, or the request budget. The number of pre-response
keepalive intervals waited MUST cover that budget. It MUST NOT drop below
`_STREAM_KEEPALIVE_MAX_COUNT` when the budget spans at least that many
keepalive intervals; when the configured budget is shorter, the count MUST
follow the budget instead, so the watchdog never outlives it.

#### Scenario: Default settings align the budget with the stuck gate

- **GIVEN** shipped defaults `sse_keepalive_interval_seconds=10`,
  `http_responses_session_bridge_stuck_gate_retire_after_seconds=300`, and
  `stream_idle_timeout_seconds=7200`
- **WHEN** the pre-response silence budget is computed
- **THEN** the budget is `300` seconds
- **AND** the pre-response keepalive count covers `300` seconds rather than the
  previous implicit `60` seconds

#### Scenario: A shorter idle timeout clamps the budget

- **GIVEN** `stream_idle_timeout_seconds=45` and a `300` second stuck gate
- **WHEN** the pre-response silence budget is computed
- **THEN** the budget is `45` seconds

### Requirement: Unmatched live upstream frames are recorded as liveness

The bridge MUST record an upstream text frame that matches no pending request,
while pending requests exist, as unmatched upstream liveness: an
`unmatched_upstream_liveness` bridge-event marker and a per-session counter. A subsequent `bridge_eventless_timeout` MUST report that counter so a
local matching wedge is distinguishable from a genuinely silent upstream.

Frames the bridge injects into its own downstream streams, in particular
`codex.keepalive`, MUST NOT be counted as upstream liveness.

#### Scenario: An unmatched upstream event is marked as liveness

- **GIVEN** an HTTP bridge session with one pending request
- **WHEN** an upstream response event arrives that matches no pending request
- **THEN** an `unmatched_upstream_liveness` bridge event is logged
- **AND** the session's unmatched upstream liveness counter increases

#### Scenario: A local keepalive frame is not upstream liveness

- **GIVEN** an HTTP bridge session with one pending request
- **WHEN** a `codex.keepalive` frame is processed
- **THEN** no `unmatched_upstream_liveness` bridge event is logged
- **AND** the session's unmatched upstream liveness counter is unchanged

### Requirement: Local bridge resets are reported as local

The bridge MUST identify local recovery resets as local bridge resets. When it
tears down and rebuilds its own upstream session (durable fresh replay,
context-overflow fresh turn, context-overflow rollover, or local
previous-response rebind), the terminal error message it settles pending
requests with MUST NOT claim the upstream websocket closed.

Reporting a local reset MUST remain account-health neutral for anchored
`stream_incomplete` settlements, exactly as the upstream-close wording was.

#### Scenario: Local recovery does not report an upstream close

- **GIVEN** the bridge performs any local reset-and-retry recovery
- **WHEN** it settles the pending requests of the session it is discarding
- **THEN** the settled error message says the bridge reset the session locally
- **AND** the message does not contain `Upstream websocket closed`
- **AND** the settlement does not mark the account unhealthy
