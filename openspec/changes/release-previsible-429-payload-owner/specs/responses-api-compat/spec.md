## MODIFIED Requirements

### Requirement: Account-bound retries remain on their dispatch owner

The proxy MUST bind a Responses request body that is not a canonical
account-neutral fresh replay to the account that first receives that exact
body. Every later selection for that request MUST treat the dispatch owner as a
strict required account across HTTP streaming, HTTP bridge, and direct
WebSocket transports, except when the first dispatch returns a classified
pre-visible rate-limit or quota rejection while owner registration is pending.

The proxy MUST NOT exclude the dispatch owner and send the retained body to a
different account during stale-anchor recovery, retryable account failure,
Trusted Access migration or degradation, bridge reconnect, or WebSocket account
switching. If the required owner is unavailable, the proxy MUST fail closed
without dispatching the retained body to another account. This prohibition does
not apply when a classified pre-visible rate-limit or quota rejection prevents
the pending dispatch owner from being established.

The proxy MAY perform one forced authentication refresh and replay a retained
account-bound body on the same dispatch owner. It MUST NOT use that refresh to
exclude the owner or migrate the body to another account, and a permanent
authentication failure MUST remain terminal for the bound body.

The proxy MAY clear the dispatch-owner binding only after verified recovery
replaces the exact wire body and the replacement passes the canonical
account-neutral-fresh-replay predicate. Removing `previous_response_id` alone
MUST NOT make retained account-scoped input portable.

Proxy-owned operation metadata that will be added at the send boundary MUST
remain bound to the current account unless an explicit operation-rebind path
replaces that identity before account selection. Installing a verified fresh
body and clearing its dispatch-owner binding MUST occur as one state
transition.

#### Scenario: Accepted encrypted reasoning remains on its dispatch owner

- **GIVEN** account A has accepted a Responses request containing encrypted
  reasoning or another account-scoped retained item and become its dispatch
  owner
- **WHEN** a pre-visible retry excludes account A or requests a differently
  authorized account
- **THEN** the proxy does not dispatch the retained body to account B
- **AND** the retry fails closed when account A is unavailable

#### Scenario: Rejected encrypted reasoning may fail over before ownership

- **GIVEN** account A first receives a Responses request containing encrypted
  reasoning and no independently established required account owner
- **WHEN** account A returns a classified rate-limit or quota rejection before
  any downstream-visible response event while owner registration is pending
- **THEN** the proxy does not establish account A as the dispatch owner
- **AND** normal retry selection may dispatch the exact unchanged ciphertext on
  account B

#### Scenario: Verified account-neutral fresh replay may change accounts

- **GIVEN** verified recovery removes a stale continuation anchor
- **AND** the exact replacement body contains only canonical account-neutral
  fresh input
- **WHEN** normal retry selection chooses account B
- **THEN** the proxy may dispatch the replacement body to account B

#### Scenario: Confirmed pre-dispatch failure does not create an owner

- **GIVEN** account A is selected for a nonportable Responses body
- **WHEN** transport evidence confirms the request failed before any upstream
  bytes were dispatched
- **THEN** the proxy does not record account A as the dispatch owner
- **AND** normal retry selection may dispatch the body first on account B

#### Scenario: HTTP bridge preserves payload ownership

- **GIVEN** an HTTP bridge request has already dispatched a nonportable body to
  account A
- **WHEN** pre-created recovery or reconnect selection excludes account A
- **THEN** the bridge does not submit that body on account B

#### Scenario: Direct WebSocket preserves payload ownership

- **GIVEN** a direct WebSocket request has already dispatched a nonportable body
  to account A
- **WHEN** retry handling prepares an account switch
- **THEN** the proxy rejects the switch unless the exact replacement body is a
  canonical account-neutral fresh replay

#### Scenario: Bound authentication refresh stays on the owner

- **GIVEN** a nonportable body is bound to account A
- **WHEN** account A reports a refreshable authentication failure before
  visible output
- **THEN** the proxy may refresh and replay once on account A
- **AND** it does not dispatch the retained body to account B

#### Scenario: HTTP bridge operation identity remains on its owner

- **GIVEN** an HTTP bridge retry retains a proxy-owned operation identity
- **AND** no explicit operation rebind has replaced that identity
- **WHEN** retry selection evaluates another account
- **THEN** the bridge requires the current operation owner

#### Scenario: Existing settlement ordering is unchanged

- **GIVEN** an API-key reservation requires settlement during the failed retry
- **WHEN** account health is updated
- **THEN** required settlement still completes before deferred health writes

## ADDED Requirements

### Requirement: Pre-visible rate-limit or quota rejection does not establish a transient dispatch owner

Notwithstanding the account-bound retry requirement, a pre-visible rejection
classified as rate-limit or quota MUST NOT establish a new dispatch-owner
binding while owner registration is pending. This exception MUST NOT clear or
move an independently established file, previous-response, turn-state, or
existing dispatch owner.

#### Scenario: Classified HTTP 429 does not establish a transient owner

- **GIVEN** a streaming Responses body is not a canonical account-neutral fresh
  replay
- **AND** the body has no independently established required account owner
- **AND** account B is eligible and the request retry budget remains
- **WHEN** account A rejects the request with an HTTP 429 classified as
  rate-limit or quota before any response event
- **THEN** the proxy does not record account A as the dispatch owner
- **AND** the proxy MUST attempt dispatch on account B before returning account
  A's 429

#### Scenario: First-event limit rejection does not establish a transient owner

- **GIVEN** a streaming Responses body is not a canonical account-neutral fresh
  replay
- **AND** the body has no independently established required account owner
- **AND** account B is eligible and the request retry budget remains
- **WHEN** account A's first upstream event is `response.failed` with a code
  classified as rate-limit or quota
- **THEN** the proxy does not expose that event or record account A as the
  dispatch owner
- **AND** the proxy MUST attempt dispatch on account B

#### Scenario: Existing required owner remains fail-closed after a limit rejection

- **GIVEN** a streaming Responses body has a file, previous-response,
  turn-state, or existing dispatch owner on account A
- **WHEN** account A returns a pre-visible rejection classified as rate-limit or
  quota
- **THEN** the proxy does not clear or move that owner
- **AND** the retained body is not dispatched on account B
