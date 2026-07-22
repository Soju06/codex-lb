## MODIFIED Requirements

### Requirement: Upstream websocket drops penalize affected accounts

When an upstream websocket closes while one or more streamed response requests are pending and have not reached a terminal event, the proxy MUST record a transient upstream error for the account before signaling failure for those pending requests. This includes an unclassified receive close, which MUST settle as `stream_incomplete`. The sole account-neutral exception is a close that carries the classified process-wide `proxy_network_unavailable` failure; that failure MUST retain its network error code and MUST NOT be replayed when upstream delivery is ambiguous. For other closes, the proxy MUST surface `stream_incomplete` to affected pending requests except when a direct Responses WebSocket request has already successfully emitted a finite integer `sequence_number`. For that sequenced direct-WebSocket case, the proxy MUST record the request outcome as `stream_incomplete` without emitting a synthetic terminal frame under the active response id, then MUST close the downstream WebSocket with code 1011.

#### Scenario: unclassified websocket receive close penalizes the account

- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** the upstream receive path closes or raises without a classified
  process-wide network error before a terminal response event
- **THEN** the pending request fails with `stream_incomplete`
- **AND** the account receives a transient upstream failure signal for routing

#### Scenario: classified process-wide network receive failure is neutral

- **GIVEN** a streamed response request is pending on an upstream websocket
- **WHEN** the receive path reports `proxy_network_unavailable` before a
  terminal response event
- **THEN** the pending request retains `proxy_network_unavailable`
- **AND** the account does not receive a transient upstream failure signal
- **AND** the request is not transparently replayed
