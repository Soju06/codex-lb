# responses-api-compat Delta Specification

## ADDED Requirements

### Requirement: Connect-phase websocket transport failures surface without account penalty

When a direct Responses websocket upstream connect attempt fails with a
server-level transient transport error — a classified `upstream_unavailable`
or `upstream_websocket_handshake_failed` failure whose HTTP status is absent
or 5xx **and** whose failure provenance is the websocket open itself
(`failure_phase = "connect"`) — the proxy MUST surface the classified failure
to the client on that attempt. It MUST NOT record account failure health for
the selected account and MUST NOT rotate to another account, because the
failure is evidence about the websocket transport, not the account, and
penalizing the account starves hard-affinity selection for the client's HTTP
retry of the same turn. Failures that merely share the
`upstream_unavailable` envelope without connect provenance — OAuth refresh
transport errors in particular — and account-scoped connect failures
(authentication, rate limit, quota, and any sub-5xx classification) MUST
retain the existing classify-penalize-failover behavior.

#### Scenario: websocket connect timeout surfaces without penalty

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the upstream websocket open fails with a 5xx classified `upstream_unavailable` transport error carrying connect provenance
- **THEN** the failure surfaces to the client on the first attempt
- **AND** no transient account error is recorded for the selected account
- **AND** no other account is consumed by failover for that attempt

#### Scenario: OAuth refresh transport failure keeps account failover

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the account's token refresh fails with a transport error converted to a 502 `upstream_unavailable` without connect provenance
- **THEN** the failure is classified and recorded against the account
- **AND** the connect series proceeds with its existing failover decision toward healthy accounts

#### Scenario: account-scoped connect failure keeps the failover path

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the upstream connect fails with an account-scoped error such as HTTP 401
- **THEN** the failure is classified and recorded against the account
- **AND** the connect series proceeds with its existing failover decision

### Requirement: Websocket handshake denial steers Codex clients to HTTP during websocket outages

Codex clients activate their session-scoped HTTP transport fallback only when
the websocket handshake is rejected with HTTP 426 (`Upgrade Required`);
in-band error events — regardless of embedded status — retry on the websocket
transport. After a connect-phase transient websocket transport failure, or
after a websocket open consumes the request budget without completing, the
proxy MUST deny new Responses websocket handshakes with HTTP 426 for a
bounded window (60 seconds), and MUST clear that denial state on the next
successful upstream websocket connect so the websocket transport resumes
automatically. While `upstream_stream_transport` is pinned to `"http"`, the
proxy MUST deny Responses websocket handshakes with HTTP 426 unconditionally.
The denial MUST NOT apply to the realtime websocket surfaces, whose upstream
is distinct.

#### Scenario: handshake denied while the transport-failure marker is armed

- **GIVEN** a connect-phase websocket transport failure occurred within the denial window
- **WHEN** a client opens a new Responses websocket handshake
- **THEN** the handshake is denied with HTTP 426
- **AND** the client's session-scoped HTTP transport fallback can activate

#### Scenario: budget-exhausted websocket open arms the denial state

- **GIVEN** the request budget expires while the upstream websocket open is stalled
- **WHEN** the budget-exhausted failure is emitted to the client
- **THEN** the transport-failure denial state is armed for subsequent handshakes

#### Scenario: handshake accepted after the denial window expires

- **GIVEN** the last connect-phase websocket transport failure is older than the denial window
- **WHEN** a client opens a new Responses websocket handshake
- **THEN** the handshake is accepted and the websocket transport is probed again

#### Scenario: pinned HTTP upstream transport denies websocket handshakes

- **GIVEN** `upstream_stream_transport` is pinned to `"http"`
- **WHEN** a client opens a Responses websocket handshake
- **THEN** the handshake is denied with HTTP 426

### Requirement: HTTP responses paths degrade to raw HTTP while the websocket transport is unavailable

The HTTP responses bridge holds upstream websocket sessions, so a pinned
`"http"` upstream transport MUST bypass the bridge and stream over raw HTTP.
While the websocket transport-failure denial state is armed, bridged and raw
HTTP Responses requests MUST pin the upstream transport to `"http"` and MUST
bypass the bridge, so a sticky follow-up that a client moved to the HTTP
route cannot resolve back onto the unavailable websocket upstream. When
bridge session creation fails with a server-level transient
`upstream_unavailable` error carrying pre-submit session-creation provenance
**and** connect-phase transport provenance (`failure_phase = "connect"`),
before any line reached the client and with no unsettled API-key usage
reservation, the proxy MUST retry the turn over raw HTTP with the upstream
transport pinned to `"http"` for that request. A pre-submit failure that
shares the `upstream_unavailable` envelope without connect provenance — an
exhausted token-refresh loop in particular — is account evidence and MUST
propagate unchanged. The fallback MUST NOT replay
a failure without pre-submit provenance (the turn may already have
dispatched upstream), MUST NOT run after any line reached the client, MUST
NOT run while an API-key usage reservation is unsettled (reservation
settlement owns that path), and MUST NOT absorb non-transient failures.

#### Scenario: pinned HTTP upstream transport bypasses the bridge

- **GIVEN** the HTTP responses bridge is enabled and `upstream_stream_transport` is pinned to `"http"`
- **WHEN** the proxy receives a bridged Responses request
- **THEN** the bridge is bypassed and the request streams over raw HTTP

#### Scenario: armed transport-failure marker forces the HTTP upstream

- **GIVEN** the websocket transport-failure denial state is armed
- **WHEN** the proxy receives a Responses request on the HTTP route
- **THEN** the bridge is bypassed and the upstream transport is pinned to `"http"` for that request

#### Scenario: pre-submit bridge session-creation failure falls back to raw HTTP

- **GIVEN** the HTTP responses bridge is enabled with the default upstream transport
- **WHEN** bridge session creation fails with a 5xx classified `upstream_unavailable` error carrying pre-submit provenance before any line reached the client
- **THEN** the turn is retried over raw HTTP with the upstream transport pinned to `"http"`

#### Scenario: refresh-provenance failures propagate unchanged

- **GIVEN** bridge session creation exhausts token refresh for the selected account and surfaces a pre-submit 502 `upstream_unavailable` without connect provenance
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay

#### Scenario: post-submit transient failures are not replayed

- **GIVEN** a bridged Responses request fails with a transient `upstream_unavailable` error without pre-submit session-creation provenance
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay

#### Scenario: partially streamed bridge turns are not replayed

- **GIVEN** a bridged Responses request already streamed at least one line to the client
- **WHEN** the bridge fails with a transient `upstream_unavailable` error
- **THEN** the failure propagates without an HTTP replay

#### Scenario: unsettled API-key reservations propagate bridge failures

- **GIVEN** a bridged Responses request holds an unsettled API-key usage reservation
- **WHEN** bridge session creation fails with a transient `upstream_unavailable` error
- **THEN** the failure propagates and reservation settlement proceeds through its existing owner
