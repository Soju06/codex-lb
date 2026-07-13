## ADDED Requirements

### Requirement: Pre-dispatch Responses requests recover from local network transitions

When a Responses request encounters a classified local DNS or host-route failure and the transport proves that request dispatch did not occur, the proxy MUST retry on the same account with bounded backoff until the attempt succeeds or the existing request budget expires. A classified token-refresh network failure MUST receive the same bounded same-account recovery only when typed transport provenance proves the refresh POST was not dispatched. Recovery MUST NOT move account-owned continuation or file state to another account. Recovery client rotation, client construction, cleanup, and sleep MUST remain inside the original monotonic deadline, and existing keepalive behavior MUST remain active while an HTTP/SSE client waits. Post-connect send or receive failures, response/body-read failures, and serialized terminal response events with uncertain upstream delivery MUST retain the account-neutral network classification but MUST NOT be transparently replayed.

#### Scenario: HTTP stream survives a temporary DNS outage

- **WHEN** a streaming Responses request fails DNS resolution before request dispatch
- **AND** DNS resolution recovers before the request budget expires
- **THEN** the proxy retries the request on the same account
- **AND** the downstream stream receives the recovered upstream response instead of a terminal network error

#### Scenario: Native WebSocket connect survives a temporary DNS outage

- **WHEN** a native Responses WebSocket request cannot open its upstream WebSocket because of a classified local network failure
- **AND** connectivity recovers before the request budget expires
- **THEN** the proxy opens the upstream WebSocket on the same account
- **AND** does not exhaust or exclude unrelated accounts

#### Scenario: Recovery remains bounded

- **WHEN** the local network does not recover before the configured request budget expires
- **THEN** the proxy terminates the request with `error.code = "upstream_request_timeout"` and message `"Proxy request budget exhausted"`
- **AND** does not extend the deadline or replay downstream-visible output

#### Scenario: Token refresh survives a temporary DNS outage

- **WHEN** token refresh for the selected account reports a classified process-network failure
- **AND** typed transport provenance proves the refresh POST was not dispatched
- **AND** connectivity recovers within the original request deadline
- **THEN** the proxy retries refresh on the same account
- **AND** does not record the network failure against the account

#### Scenario: Token refresh response failure is not replayed

- **WHEN** token refresh reports a classified process-network failure while reading the response or body
- **AND** the proxy cannot prove the refresh POST was not dispatched
- **THEN** the failure retains the account-neutral process-network code
- **AND** the proxy does not retry the possibly consumed rotating refresh token

#### Scenario: Ambiguous compact POST failure is not replayed

- **WHEN** a compact POST reports a classified process-network failure without typed pre-dispatch provenance
- **THEN** the compact failure retains the account-neutral process-network code
- **AND** the proxy does not replay, penalize, or exclude the selected account

#### Scenario: Serialized terminal network failure is not replayed

- **WHEN** an upstream stream emits a terminal response event carrying the process-network code
- **AND** the proxy cannot prove that request dispatch did not occur
- **THEN** the terminal event is surfaced without transparent replay
- **AND** the selected account's health remains unchanged

#### Scenario: Post-connect WebSocket network failure is not replayed speculatively

- **WHEN** an upstream WebSocket send or receive reports a classified process-network failure after the connection opened
- **AND** the proxy cannot prove that `response.create` was not delivered
- **THEN** the pending request fails with the account-neutral process-network code
- **AND** the proxy does not transparently replay the request

## MODIFIED Requirements

### Requirement: Upstream websocket drops penalize affected accounts

When an upstream websocket closes while one or more streamed response requests are pending and have not reached a terminal event, the proxy MUST record a transient upstream error for the account before surfacing `stream_incomplete` to those pending requests, except when the close carries a classified process-wide network failure. A classified process-wide network failure MUST remain account neutral and use its network error code.

#### Scenario: websocket closes before pending responses complete

- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** the websocket closes before a terminal response event is observed
- **AND** the close does not carry a classified process-wide network failure
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing
