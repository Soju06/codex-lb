## MODIFIED Requirements

### Requirement: Long Codex websocket turns tolerate extended upstream silence
The default compact request budget MUST be at least 180 seconds, and the default upstream stream idle timeout MUST be at least 600 seconds, so long-running Codex turns can survive expensive compaction or tool execution without a local proxy watchdog ending the turn prematurely. Responses streams over both HTTP and WebSocket transports MUST use `http_responses_stream_request_budget_seconds` when it is configured; they MUST fall back to `proxy_request_budget_seconds` only when no stream-specific budget is available.

`compact_request_budget_seconds`, `stream_idle_timeout_seconds`, `proxy_request_budget_seconds` and `http_responses_stream_request_budget_seconds` are dashboard-managed (`configuration-tiers`): each has a nullable `dashboard_settings` column of the same name whose non-NULL value MUST override the process environment value, which in turn overrides the code default. Consumers MUST read the effective value from the `SettingsCache` snapshot bound at the request or WebSocket entry point and MUST NOT query the database per request or per event. The environment variables remain as deprecated fallbacks and MUST NOT be copied into the column by the server; a client that echoes the effective values of a `GET /api/settings` response back through `PUT` stores them as explicit dashboard values (clients MUST send only the fields they intend to change). `GET /api/settings` MUST report any environment value the `Settings` model accepts, including one outside the bounds `PUT` enforces.

Background consumers derived from the stream budget (the quota warm-up claim lease, which floors at the stream budget) MUST resolve the effective value from a dashboard snapshot the scheduler tick already holds, not from the environment alone. The background warm-up probe itself MUST stream under that same snapshot, so a healthy probe still provably outlives its claim lease when the dashboard budget is below the environment value. `upstream_connect_timeout_seconds` MUST NOT exceed the effective `http_responses_stream_request_budget_seconds` (`upstream-connect-within-stream-budget`); `PUT /api/settings` MUST reject a change that introduces that violation with `400 timeout_invariant_violation`, alongside the existing `admission-wait-within-stream-budget` rule.

#### Scenario: compact and stream watchdog defaults leave room for long turns
- **WHEN** the service starts with default configuration
- **THEN** `compact_request_budget_seconds` is at least 180 seconds
- **AND** `stream_idle_timeout_seconds` is at least 600 seconds

#### Scenario: WebSocket Responses stream uses the stream-specific request budget
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **WHEN** a native WebSocket Responses stream computes its request deadline
- **THEN** the stream budget is 7200 seconds
- **AND** the generic 600 second proxy request budget does not terminate the turn

#### Scenario: WebSocket reconnect keeps the stream-specific deadline
- **GIVEN** `proxy_request_budget_seconds = 600`
- **AND** `http_responses_stream_request_budget_seconds = 7200`
- **AND** a native WebSocket Responses request needs to reconnect after more than 600 seconds but less than 7200 seconds
- **WHEN** the reconnect performs account selection and opens its replacement upstream WebSocket
- **THEN** both operations remain bounded by the original 7200-second stream deadline
- **AND** the reconnect does not fail solely because the generic 600-second budget elapsed

#### Scenario: Dashboard value overrides startup environment
- **GIVEN** `CODEX_LB_PROXY_REQUEST_BUDGET_SECONDS=600` in the process environment and an operator has stored `900` for `proxy_request_budget_seconds` through `PUT /api/settings`
- **WHEN** a new request computes its request deadline on any replica
- **THEN** the deadline uses the 900 second dashboard value
- **AND** `GET /api/settings` reports `proxyRequestBudgetSeconds: 900` with `provenance.proxy_request_budget_seconds.source = "dashboard"`

#### Scenario: Clearing the dashboard value returns to the environment
- **GIVEN** the same deployment
- **WHEN** the operator sends `PUT /api/settings` with `proxyRequestBudgetSeconds: null`
- **THEN** the column becomes NULL, new requests use the 600 second environment value, and the provenance source becomes `"env"` (or `"default"` when the environment matches the code default)

#### Scenario: Timeout invariants are enforced on the effective values
- **GIVEN** the environment-only admission wait is 10 seconds
- **WHEN** the operator sends `PUT /api/settings` with `proxyRequestBudgetSeconds: 5`
- **THEN** the request is rejected with `400` and code `timeout_invariant_violation` naming `admission-wait-within-proxy-budget`
- **AND** a request that raises every violated budget in the same `PUT` is accepted

#### Scenario: Dashboard stream budget overrides startup environment
- **GIVEN** `CODEX_LB_HTTP_RESPONSES_STREAM_REQUEST_BUDGET_SECONDS=7200` in the process environment and an operator has stored `3600` for `http_responses_stream_request_budget_seconds` through `PUT /api/settings`
- **WHEN** a new HTTP or WebSocket Responses stream computes its request deadline on any replica
- **THEN** the stream budget is 3600 seconds
- **AND** `GET /api/settings` reports `httpResponsesStreamRequestBudgetSeconds: 3600` with `provenance.http_responses_stream_request_budget_seconds.source = "dashboard"`
- **AND** the next quota warm-up claim lease is 3600 seconds long rather than 7200
- **AND** the background warm-up probe streams under the same 3600 second budget

#### Scenario: Connect timeout is bounded by the effective stream budget
- **GIVEN** default configuration
- **WHEN** the operator sends `PUT /api/settings` with `upstreamConnectTimeoutSeconds: 100` and `httpResponsesStreamRequestBudgetSeconds: 60`
- **THEN** the request is rejected with `400` and code `timeout_invariant_violation` naming `upstream-connect-within-stream-budget`
- **AND** a `PUT` with `httpResponsesStreamRequestBudgetSeconds: 5` is rejected naming `admission-wait-within-stream-budget`
- **AND** nothing is stored in either case

### Requirement: Ambiguous HTTP bridge operations converge after owner loss

The durable HTTP bridge operation ledger MUST preserve duplicate suppression
while an operation is live or ambiguous, but an `unknown` or `acknowledged`
operation MAY transition to the terminal `abandoned` state only after its
`updated_at` is older than `max(1800 seconds,
http_responses_session_bridge_request_budget_seconds)`, no local canonical or
detached bridge request is pending for that operation, and the durable owning
session has no owner or an owner lease that has remained expired for at least
one additional durable lease period. The budget term is the effective
dashboard-managed value: a non-NULL
`dashboard_settings.http_responses_session_bridge_request_budget_seconds`
overrides the environment value, which overrides the 7200 second code default.

The maintenance sweep runs from the ring heartbeat outside any request
binding, so it MUST resolve that budget from one `SettingsCache` snapshot read
per sweep — never per candidate row and never inside the bridge registry lock.
When the snapshot cannot be read the sweep MUST log a warning and fall back to
the environment value rather than skipping the pass.

An ownerless session produced by a graceful lease release MUST remain
ineligible until its recorded `lease_expires_at` has aged through that same
durable lease period. Candidate reads MUST lock the operation and session rows
on PostgreSQL on both the normal predicate path and the oversized-protection
bounded-page path.

The transition MUST atomically compare the operation state, `updated_at`,
durable event-spool progress, session owner instance, and owner epoch. A
concurrent recovery claim, owner renewal/takeover, or status proof MUST win
over abandonment. A persisted nonterminal event MUST advance durable
event-spool progress; if that event commits after candidate selection but
before the abandonment compare-and-set, the compare-and-set MUST affect zero
rows. The operation row and all event history MUST remain available for normal
retention. The proxy MUST NOT automatically resend or cancel the ambiguous
upstream operation.
The maintenance sweep MUST render no more than the repository's database-safe
number of protected operation IDs in one expanding predicate. If the local
protection snapshot exceeds that bound, the sweep MUST use bounded candidate
pages and filter the full protection set without truncating it; every protected
operation MUST remain unchanged while unrelated eligible operations MAY still
transition. Each oversized-protection sweep MUST inspect no more than a finite
scan budget, return a keyset cursor for the last inspected eligible row, and
the next maintenance sweep MUST resume after that cursor. Once the eligible
range is exhausted, the cursor MUST wrap to the beginning so later rows cannot
be starved by a protected prefix.

#### Scenario: stale ownerless operation is abandoned

- **GIVEN** an operation is `unknown` or `acknowledged`
- **AND** its `updated_at` is older than the bounded inactivity cutoff
- **AND** no canonical or detached local bridge request is pending for it
- **AND** its durable session owner is absent or its lease is expired
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation becomes terminal `abandoned`
- **AND** its operation row and event history remain intact
- **AND** no upstream request is dispatched by the sweep

#### Scenario: oversized protected prefix advances across sweeps

- **GIVEN** the local protection snapshot exceeds the database-safe bind limit
- **AND** more stale eligible rows are protected than one sweep's finite scan
  budget
- **AND** a later stale eligible operation is not protected
- **WHEN** the bridge maintenance sweep runs
- **THEN** it inspects no more than the finite scan budget in that sweep
- **AND** it preserves a keyset cursor after the inspected protected prefix
- **AND** a later sweep resumes after that cursor and may abandon the later
  unprotected operation
- **AND** the protected operations remain unchanged

#### Scenario: oversized protection keeps the session lock fence

- **GIVEN** the local protection snapshot requires the bounded-page path
- **WHEN** PostgreSQL selects an abandonment candidate
- **THEN** the candidate operation and owning session rows remain locked until
  the abandonment transaction commits
- **AND** a concurrent renewal or takeover cannot commit behind the stale
  candidate snapshot

#### Scenario: live owner is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session has an unexpired owner lease
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains `unknown` or `acknowledged`

#### Scenario: a brief owner-renewal lapse is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session owner lease expired less than one durable lease
  period ago
- **WHEN** another replica runs the bridge maintenance sweep
- **THEN** the operation remains `unknown` or `acknowledged`
- **AND** the original owner may still renew or finalize it

#### Scenario: a recent ownerless release is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** its durable session was released to ownerless less than one durable
  lease period ago
- **WHEN** another replica runs the bridge maintenance sweep
- **THEN** the operation remains `unknown` or `acknowledged`
- **AND** the releasing replica may finish its pending settlement

#### Scenario: pending local work is not abandoned

- **GIVEN** an ambiguous operation is older than the inactivity cutoff
- **AND** a canonical or detached local bridge generation still has a pending
  request state for that operation
- **WHEN** the bridge maintenance sweep runs
- **THEN** the operation remains unchanged

#### Scenario: concurrent recovery wins

- **GIVEN** a stale `unknown` operation is selected for abandonment
- **WHEN** a recovery claim changes it to `submitted` before the CAS commits
- **THEN** the abandonment affects zero rows
- **AND** the operation remains `submitted`

#### Scenario: concurrent status proof wins

- **GIVEN** a stale `acknowledged` operation is selected for abandonment
- **WHEN** a nonterminal status event is durably appended before the
  abandonment compare-and-set commits
- **THEN** the abandonment affects zero rows
- **AND** the operation remains `acknowledged`
- **AND** the appended event remains available in the operation history

#### Scenario: late status proof cannot revive abandonment

- **GIVEN** an operation has become `abandoned`
- **WHEN** a late upstream event or status callback attempts to update it
- **THEN** the write is rejected or becomes a no-op
- **AND** the operation remains `abandoned`

#### Scenario: abandoned continuation requests full-history recovery

- **GIVEN** operation admission finds an existing operation in `abandoned`
- **WHEN** a client sends the same continuation again
- **THEN** the proxy does not claim, reset, or dispatch that operation
- **AND** it returns HTTP 400 with error code
  `previous_response_not_found` and parameter `previous_response_id`
- **AND** the error uses the canonical continuity contract that allows Codex
  to retry without `previous_response_id`

#### Scenario: abandoned hard turn-state requests full-history recovery

- **GIVEN** operation admission finds an existing hard turn-state operation in
  `abandoned`
- **AND** the request has no `previous_response_id`
- **WHEN** the client sends the same continuation again
- **THEN** the proxy does not claim, reset, or dispatch that operation
- **AND** it returns HTTP 400 with error code `previous_response_not_found`
  without a `previous_response_id` parameter
- **AND** the error instructs Codex to discard the hard continuity anchor and
  resend full history

#### Scenario: dashboard bridge budget sets the inactivity cutoff

- **GIVEN** the process environment sets a 120 second bridge request budget
- **AND** an operator has stored `10800` for
  `http_responses_session_bridge_request_budget_seconds` through
  `PUT /api/settings`
- **WHEN** the bridge maintenance sweep runs on any replica
- **THEN** the inactivity cutoff is 10800 seconds before the sweep time
- **AND** not the `max(1800, 120)` = 1800 seconds the environment alone implies

#### Scenario: snapshot failure keeps the environment budget

- **GIVEN** the `SettingsCache` snapshot cannot be read during a sweep
- **WHEN** the bridge maintenance sweep runs
- **THEN** the sweep still runs with the `max(1800 seconds, environment budget)`
  cutoff
- **AND** a warning is logged

### Requirement: The pre-response silence budget is settings-derived

The pre-response silence budget MUST be a named quantity derived from
configuration, not the implicit product of `_STREAM_KEEPALIVE_MAX_COUNT` and
`sse_keepalive_interval_seconds`.

The budget MUST be the minimum of the fixed owner-side stuck gate
(`HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS`, 300 seconds; not a runtime
setting), `stream_idle_timeout_seconds`, and
`http_responses_session_bridge_request_budget_seconds`, so that the downstream
pre-response watchdog can never outlive the owner-side stuck gate, the
configured idle budget, or the request budget. The two settings-derived terms
MUST be read as their effective dashboard-managed values from the snapshot
bound to the request, a non-NULL `dashboard_settings` column of the same name
overriding the environment value. The number of pre-response
keepalive intervals waited MUST cover that budget. It MUST NOT drop below
`_STREAM_KEEPALIVE_MAX_COUNT` when the budget spans at least that many
keepalive intervals; when the configured budget is shorter, the count MUST
follow the budget instead, so the watchdog never outlives it.

#### Scenario: Default settings align the budget with the stuck gate

- **GIVEN** shipped defaults `sse_keepalive_interval_seconds=10` and
  `stream_idle_timeout_seconds=7200`, and the fixed `300` second stuck gate
- **WHEN** the pre-response silence budget is computed
- **THEN** the budget is `300` seconds
- **AND** the pre-response keepalive count covers `300` seconds rather than the
  previous implicit `60` seconds

#### Scenario: A shorter idle timeout clamps the budget

- **GIVEN** `stream_idle_timeout_seconds=45` and a `300` second stuck gate
- **WHEN** the pre-response silence budget is computed
- **THEN** the budget is `45` seconds

#### Scenario: Dashboard bridge budget is the term compared

- **GIVEN** the process environment sets a `7200` second bridge request budget
  and an operator has stored `650` through `PUT /api/settings`
- **AND** `stream_idle_timeout_seconds=7200` and the fixed `300` second stuck
  gate
- **WHEN** the pre-response silence budget is computed for a request
- **THEN** the bridge budget term is `650` seconds (the dashboard value)
- **AND** the budget is `300` seconds
