# responses-api-compat Delta Specification

## ADDED Requirements

### Requirement: Connect-phase websocket transport failures surface without account penalty

The direct upstream websocket open MUST stamp host-scoped transport
provenance on the failures that prove the websocket transport itself did not
come up: a connect timeout, an invalid handshake, a 5xx upgrade rejection,
and a connect-phase network error other than host-wide network loss. It MUST
NOT stamp that provenance on failures that are scoped to something narrower
than the transport — credential-scoped handshake rejections (401, 403, 429
and any other sub-5xx status), TLS verification failures, host-wide network
loss, and every routed-proxy open, which proves nothing beyond the health of
one account's proxy endpoint.

When a Responses websocket upstream connect attempt fails carrying that
transport provenance, and the failure is not confirmed pre-dispatch route
evidence, the proxy MUST surface the classified failure to the client on that
attempt. It MUST NOT record account failure health for the selected account
and MUST NOT rotate to another account, because the failure is evidence about
the websocket transport, not the account, and penalizing the account starves
hard-affinity selection for the client's HTTP retry of the same turn.

Classification MUST key on that provenance rather than on the sanitized error
code, which cannot carry it in either direction: the Responses policy
preserves the upstream handshake body, so a direct 5xx upgrade rejection
surfaces as `upstream_error` or whatever code the edge returned, while OAuth
refresh transport errors, routed handshakes and TLS failures all share the
`upstream_unavailable` envelope. Failures without transport provenance MUST
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

#### Scenario: direct 5xx handshake rejection is transport evidence

- **GIVEN** a direct Responses websocket connect series selected an account
- **WHEN** the upstream rejects the upgrade with HTTP 503 and an unstructured body, which the client converts to code `upstream_error`
- **THEN** the failure carries websocket transport provenance
- **AND** it surfaces without an account penalty despite not matching a websocket-specific error code

#### Scenario: routed handshake failure keeps account failover

- **GIVEN** accounts reach upstream through different proxy routes
- **WHEN** one account's routed websocket open fails with an HTTP 5xx handshake
- **THEN** the failure carries no websocket transport provenance
- **AND** the connect series proceeds with its existing account/route failover decision instead of denying handshakes instance-wide

#### Scenario: TLS verification failure stays out of the transport fallback

- **GIVEN** a Responses websocket connect series selected an account
- **WHEN** the upstream websocket open fails TLS certificate verification
- **THEN** the failure carries no websocket transport provenance
- **AND** handshakes are not denied, because a raw HTTP retry reaches the same invalid TLS configuration

### Requirement: Websocket handshake denial steers Codex clients to HTTP during websocket outages

Codex clients activate their session-scoped HTTP transport fallback only when
the websocket handshake is rejected with HTTP 426 (`Upgrade Required`);
in-band error events — regardless of embedded status — retry on the websocket
transport. After a websocket connect failure carrying transport provenance,
or after a websocket open consumes the request budget once the direct
upstream connector itself has begun, the proxy MUST deny new Responses
websocket handshakes with HTTP 426 for a bounded window (60 seconds), and
MUST clear that denial state on the next successful direct upstream
websocket connect so the websocket transport resumes automatically. Clearing
is direct-scoped for the same reason arming is: a routed success proves only
that one account's proxy endpoint is healthy and MUST NOT readmit handshakes
during a direct-upstream outage. A deployment whose accounts are all routed
therefore never arms or clears the state, and a mixed one still expires it on
the bounded window. A request budget
that expires before the direct connector begins MUST NOT arm the denial
state: while the open is still waiting on local websocket-connect admission
or resolving the account's route that is local contention, and forcing every
client onto HTTP would amplify the overload it came from; and a stalled
routed open is route-scoped for the same reason a routed handshake failure
is, with no error for the routed exclusion to act on because the budget
cancels the open rather than failing it. While `upstream_stream_transport` is pinned to `"http"`, the
proxy MUST deny Responses websocket handshakes with HTTP 426 unconditionally.
The denial MUST NOT apply to the realtime websocket surfaces, whose upstream
is distinct, nor to a handshake carrying a required-capability header:
capability routing resolves only on this transport, so the downgrade would
send the session to an HTTP path that rejects the same capability, and the
client treats the switch as session-scoped and never returns.

Because a required capability may also be carried in a `response.create`
`client_metadata` field, which no handshake can observe, the HTTP responses
path MUST reject a capability signal found there with the same
transport-unsupported error it returns for the header. Capability resolution
fails closed to security-work-authorized accounts on the websocket path and
has no equivalent constraint on the HTTP path, so a metadata-only signal that
reached HTTP would otherwise enter ordinary account selection unconstrained.

#### Scenario: handshake denied while the transport-failure marker is armed

- **GIVEN** a connect-phase websocket transport failure occurred within the denial window
- **WHEN** a client opens a new Responses websocket handshake
- **THEN** the handshake is denied with HTTP 426
- **AND** the client's session-scoped HTTP transport fallback can activate

#### Scenario: budget-exhausted websocket open arms the denial state

- **GIVEN** the request budget expires while the upstream websocket connector is stalled
- **WHEN** the budget-exhausted failure is emitted to the client
- **THEN** the transport-failure denial state is armed for subsequent handshakes

#### Scenario: budget exhausted in local admission does not arm the denial state

- **GIVEN** the request budget is shorter than the local websocket-connect admission wait
- **WHEN** the budget expires before the upstream connector begins
- **THEN** the failure surfaces as local admission evidence
- **AND** the transport-failure denial state is not armed

#### Scenario: budget exhausted in a routed connector does not arm the denial state

- **GIVEN** an account resolves to a proxy route and its routed websocket open stalls
- **WHEN** the request budget expires while that routed connector is running
- **THEN** the transport-failure denial state is not armed, because only that account's proxy endpoint was shown unhealthy

#### Scenario: handshake accepted after the denial window expires

- **GIVEN** the last connect-phase websocket transport failure is older than the denial window
- **WHEN** a client opens a new Responses websocket handshake
- **THEN** the handshake is accepted and the websocket transport is probed again

#### Scenario: a routed success does not clear the denial state

- **GIVEN** the transport-failure denial state is armed by direct-upstream evidence
- **WHEN** an account whose route resolves to a proxy endpoint opens its upstream websocket successfully
- **THEN** the denial state stays armed until a direct upstream connect succeeds or the bounded window expires

#### Scenario: pinned HTTP upstream transport denies websocket handshakes

- **GIVEN** `upstream_stream_transport` is pinned to `"http"`
- **WHEN** a client opens a Responses websocket handshake
- **THEN** the handshake is denied with HTTP 426

#### Scenario: capability handshakes are never downgraded

- **GIVEN** the transport-failure denial state is armed
- **WHEN** a client opens a Responses websocket handshake carrying a required-capability header
- **THEN** the handshake is accepted rather than denied with 426, because capability routing exists only on this transport

#### Scenario: a metadata-only capability signal is rejected over HTTP

- **GIVEN** a Responses request arrives on the HTTP path carrying a required capability only in `client_metadata`
- **WHEN** the request is admitted
- **THEN** it is rejected with the transport-unsupported error rather than entering ordinary account selection without the authorization constraint

### Requirement: HTTP responses paths degrade to raw HTTP while the websocket transport is unavailable

The HTTP responses bridge holds upstream websocket sessions, so a pinned
`"http"` upstream transport MUST bypass the bridge and stream over raw HTTP.
While the websocket transport-failure denial state is armed, bridged and raw
HTTP Responses requests MUST pin the upstream transport to `"http"` and MUST
bypass the bridge, so a sticky follow-up that a client moved to the HTTP
route cannot resolve back onto the unavailable websocket upstream.

When bridge session creation fails carrying pre-submit session-creation
provenance **and** the same websocket transport provenance the failover
decision classifies on, before any line reached the client and with no
unsettled API-key usage reservation, the proxy MUST retry the turn over raw
HTTP with the upstream transport pinned to `"http"` for that request. That
decision MUST use the transport provenance rather than the sanitized error
code, which a direct 5xx bridge connect surfaces as `upstream_error` or
whatever the edge returned. A pre-submit failure without transport
provenance — an exhausted token-refresh loop or a routed handshake failure in
particular — is account or route evidence and MUST propagate unchanged.

Bridge session creation runs its own pre-dispatch failover and never reaches
the websocket failover decision, so when that fallback accepts a failure the
websocket transport classifier also recognizes, the proxy MUST arm the
transport-failure denial state; otherwise bridge-only traffic leaves it clear
and every later request re-attempts the unavailable websocket bridge before
falling back.

The raw-HTTP replay carries the incoming payload, not the bridge's prepared
payload, and the raw path never injects a response anchor. When bridge
session creation prepared a continuity anchor the incoming payload does not
carry, the proxy MUST NOT replay the turn over raw HTTP: doing so would send
the new turn alone and silently drop the prior conversation. The fallback
MUST NOT replay a failure without pre-submit provenance (the turn may already
have dispatched upstream), MUST NOT run after any line reached the client,
MUST NOT run while an API-key usage reservation is unsettled (reservation
settlement owns that path), and MUST NOT absorb non-transient failures.

When the bridge retry circuit's pre-dispatch submission gate suppresses a
request whose state is provably undispatched — no client or proxy-injected
continuation identity, no payload `conversation`, no file account pin, no
send attempt recorded for the request, and none of the unambiguous-boundary
markers (`response_id`, response events, downstream visibility, or a prior
replay) — the resulting
cooldown failure MUST carry the same pre-submit provenance and degrade to the
raw-HTTP fallback instead of a bounded 503. That undispatched proof MUST come
from state that is false before an actual send; a marker set optimistically
at request construction proves nothing and would make the fallback
unreachable. A cooldown suppression of an ambiguous continuation MUST keep
the bounded 503 with its retry hint.

That suppression MUST be identified by provenance the gate attaches, never by
its error code. An ordinary pre-submit budget exhaustion emits the same
`upstream_request_timeout` and collects the same pre-submit provenance while
session creation unwinds, but it is admission-queue or host-network evidence:
replaying it would double every request exactly when the instance is
saturated, and would feed a doomed raw-HTTP attempt into its own
process-network recovery wait. A budget exhaustion MUST therefore propagate
unchanged, and a cooldown suppression MUST NOT arm the websocket
transport-failure denial state, being bridge-scoped rather than transport
evidence.

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

#### Scenario: direct 5xx bridge connect falls back on its provenance

- **GIVEN** bridge session creation fails on a direct 5xx handshake, whose preserved upstream envelope carries the code `upstream_error`
- **WHEN** the failure reaches the bridge wrapper before any line reached the client
- **THEN** the turn is retried over raw HTTP, because the transport provenance and not the sanitized code decides

#### Scenario: routed bridge connect failures propagate unchanged

- **GIVEN** bridge session creation fails on a routed proxy handshake, which carries no transport provenance
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay and the denial state stays clear

#### Scenario: bridge connect fallback arms the denial state

- **GIVEN** bridge session creation fails with a websocket connect failure carrying transport provenance
- **WHEN** the turn is retried over raw HTTP
- **THEN** the transport-failure denial state is armed
- **AND** subsequent HTTP requests bypass the bridge and the next websocket handshake is denied with HTTP 426

#### Scenario: bridge-prepared continuity anchors are not replayed over raw HTTP

- **GIVEN** an incoming Responses request carries no `previous_response_id` and the bridge injected the durable session anchor into its prepared payload
- **WHEN** bridge session creation then fails pre-submit with a transient `upstream_unavailable` error
- **THEN** the failure propagates without an HTTP replay, because the incoming payload alone would drop the prior conversation

#### Scenario: refresh-provenance failures propagate unchanged

- **GIVEN** bridge session creation exhausts token refresh for the selected account and surfaces a pre-submit 502 `upstream_unavailable` without connect provenance
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay

#### Scenario: replay-safe cooldown suppression falls back to raw HTTP

- **GIVEN** the bridge retry circuit is cooling down and a fresh turn with no continuation identity and no dispatch markers is suppressed at the pre-dispatch submission gate
- **WHEN** the cooldown failure reaches the bridge wrapper before any line reached the client
- **THEN** the turn is retried over raw HTTP with the upstream transport pinned to `"http"`

#### Scenario: pre-submit budget exhaustion is not a cooldown suppression

- **GIVEN** bridge session creation exhausts the request budget and surfaces `upstream_request_timeout` with pre-submit provenance but no cooldown marker
- **WHEN** the failure reaches the bridge wrapper
- **THEN** the failure propagates without an HTTP replay, and the transport-failure denial state stays clear

#### Scenario: ambiguous cooldown suppression keeps the bounded 503

- **GIVEN** the bridge retry circuit is cooling down and a continuation whose delivery is ambiguous is suppressed
- **WHEN** the cooldown failure reaches the bridge wrapper
- **THEN** the bounded 503 with its retry hint propagates without an HTTP replay

#### Scenario: a conversation-scoped suppression keeps the bounded 503

- **GIVEN** a suppressed request carries a non-empty payload `conversation` but no anchor, turn-state key, file pin, or dispatch marker
- **WHEN** the cooldown failure reaches the bridge wrapper
- **THEN** the bounded 503 propagates, because `conversation` has no owner index and a raw-HTTP replay could not prove the bridge session's owner

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
