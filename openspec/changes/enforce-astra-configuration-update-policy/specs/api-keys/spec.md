## ADDED Requirements

### Requirement: Configuration updates obey API-key reasoning controls

The proxy SHALL apply allowed and enforced reasoning controls to
`configuration_update` input items as well as request-level reasoning. It
SHALL reject an update that violates those controls before forwarding it.
Historical supported updates SHALL preserve their order and request-level
cache prefix. A subscription Astra continuation using a response or
conversation anchor and an API key with `enforced_reasoning_effort` SHALL
explicitly establish that enforced configuration before processing new
input, so an unseen inherited configuration cannot bypass the current
policy. Allowed-list keys SHALL rely on per-request validation and SHALL
NOT synthesize a default effort on continuations. An omitted
request-level effort on an allowed-list continuation SHALL match the
fresh-request path. This requirement also applies to proxy-injected
anchors; repeated preparation SHALL be idempotent.

#### Scenario: An in-history update cannot evade allowed efforts

- **GIVEN** an API key allows only low reasoning
- **WHEN** a request contains configuration_update selecting high
- **THEN** the request is rejected before upstream work starts
- **AND** the error param identifies `input.<index>.reasoning.effort`

#### Scenario: Enforcement conflicts are explicit

- **GIVEN** an API key enforces low reasoning
- **WHEN** a request contains configuration_update selecting high
- **THEN** the request is rejected instead of silently applying the conflicting update

#### Scenario: An anchored continuation cannot inherit an unauthorized effort

- **GIVEN** a previous response has retained high reasoning and the current API key enforces low
- **WHEN** a subscription Astra continuation supplies an anchor without a leading configuration update
- **THEN** the proxy establishes low using a leading configuration update before the new input
- **AND** request-level reasoning and existing input order are preserved

#### Scenario: An allowed-list continuation with omitted effort matches a fresh request

- **GIVEN** an API key allows only high reasoning and does not enforce an effort
- **WHEN** a subscription Astra continuation omits request-level reasoning.effort
- **THEN** the proxy does not prepend a configuration_update
- **AND** the request is not rejected for a synthesized medium
- **AND** the same omitted-effort request without an anchor is also accepted

#### Scenario: Owner forwarding preserves client reasoning identity

- **WHEN** an enforced-ultra continuation selects ultra and is prepared and forwarded through an owner instance more than once
- **THEN** preparation retains exactly one leading configuration update selecting ultra and request-level ultra, without treating either value as max during API-key policy checks
- **AND** only final subscription wire serialization maps both ultra values to max

#### Scenario: Injected response anchors drop conversation

- **GIVEN** an Astra HTTP-bridge request that still carries `conversation`
- **WHEN** a completed identical turn supplies a `previous_response_id` anchor
- **THEN** the reconstructed payload keeps that response anchor and omits `conversation`

#### Scenario: HTTP-bridge full resend trims before the Astra reset

- **GIVEN** a previous_response_id full resend that starts with stored assistant or reasoning output followed by a tool output
- **WHEN** an enforced-effort key requires a continuation reset
- **THEN** the proxy trims that stored prefix before inserting the reset
- **AND** streaming and collected HTTP routes preserve the original full-resend item count and fingerprint for bridge completion bookkeeping
- **AND** a subsequent full resend matching that stored prefix remains eligible for continuation anchoring and fresh-replay recovery

#### Scenario: An anchored delta retains its client prefix

- **GIVEN** an enforced-effort Astra continuation whose input needs no history trimming
- **WHEN** preparation inserts a policy reset, including before a later operation-ledger anchor advance
- **THEN** live and durable completion bookkeeping SHALL retain the client input count and fingerprint without the reset
- **AND** a later client full resend SHALL still match that prefix and reuse the completed response

#### Scenario: A pre-submit HTTP fallback retains continuation policy

- **GIVEN** an anchored subscription Astra request uses an enforced-effort key without an applicable usage reservation
- **WHEN** the HTTP bridge encounters an eligible pre-submit WebSocket transport failure and retries over raw HTTP
- **THEN** the fallback SHALL trim stored replay input before applying the same continuation policy as the other HTTP paths
- **AND** the forwarded body SHALL retain exactly one required leading update and the client-supplied anchor
- **AND** Ultra SHALL retain its client identity through validation and serialize as Max only on the subscription wire
- **AND** fallback eligibility, account ownership and reservation settlement SHALL remain unchanged

#### Scenario: Injected Ultra resets survive repeated anchor advances

- **GIVEN** a proxy-injected HTTP-bridge anchor for an enforced-ultra key
- **WHEN** the same reconstructed request is prepared again after the first injection serialized as max
- **THEN** the client-plane Ultra identity is restored for policy checks
- **AND** the continuation is not rejected as max
- **AND** repeated preparation keeps one leading configuration_update, the same item count and order, and request-level Ultra
- **AND** only subscription wire serialization maps Ultra to max

#### Scenario: Non-leading updates keep client-plane efforts across repeated anchors

- **GIVEN** a turn-state request with a non-leading configuration_update
- **WHEN** continuation preparation prepends a policy update and a later advance reconstructs the request
- **THEN** each historical configuration_update is restored to its stored client-plane effort and original position
- **AND** the prepended policy update keeps the selected continuation effort

#### Scenario: A late continuation-policy rejection terminates an open stream

- **WHEN** HTTP-bridge recovery adds an anchor and Astra policy rejects the reconstructed request after HTTP streaming has started
- **THEN** the proxy SHALL emit exactly one terminal response.failed event with the policy error code, type, message and parameter
- **AND** the proxy SHALL release any reservation it still owns without dispatching the rejected request
- **AND** this behavior SHALL also apply when the policy rejection occurs during a server-owned recovery attempt
