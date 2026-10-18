## MODIFIED Requirements

### Requirement: Account-bound retries remain on their dispatch owner

The proxy MUST bind a Responses request body that is not a canonical
account-neutral fresh replay to the account that first receives that exact
body. Every later selection for that request MUST treat the dispatch owner as a
strict required account across HTTP streaming, HTTP bridge, and direct
WebSocket transports.

The proxy MUST NOT exclude the dispatch owner and send the retained body to a
different account during stale-anchor recovery, retryable account failure,
Trusted Access migration or degradation, bridge reconnect, or WebSocket account
switching. If the required owner is unavailable, the proxy MUST fail closed
without dispatching the retained body to another account.

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

#### Scenario: Encrypted reasoning remains on its first dispatch account

- **GIVEN** account A first receives a Responses request containing encrypted
  reasoning or another account-scoped retained item
- **WHEN** a pre-visible retry excludes account A or requests a differently
  authorized account
- **THEN** the proxy does not dispatch the retained body to account B
- **AND** it may dispatch only a verified account-neutral projection that
  removes the account-scoped items and passes the canonical fresh-replay gates
- **AND** the retry fails closed when no such projection is available

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

### Requirement: Unanchored full resends recover from pre-visible quota rejection

The proxy MUST permit account failover for a Responses streaming request with no
previous-response or conversation anchor, no turn-state or input-file owner, and no
single-account routing after the selected account rejects the request for quota or rate
limits before any downstream event only when it can construct an account-neutral full resend.

The replay input MUST be produced by the shared response-owned-bookkeeping projection. The
projected request MUST pass the shared account-neutral fresh-replay validation and MUST retain
completed assistant output followed by fresh user input or an exact Codex host-generated
scheduled-task heartbeat. The proxy MUST preserve the requested
model, reasoning configuration, instructions, tools, and other account-neutral controls. It
MUST clear the failed attempt's soft payload-owner marker, exclude the rejected account, and
reallocate advisory prompt-cache affinity before reselection.

The proxy MUST NOT cross accounts for a request carrying a nonblank previous-response or
conversation anchor, a turn-state owner, an input-file owner, single-account routing, an
incomplete or non-neutral transcript, or any downstream-visible output. A non-quota failure
MUST retain its existing retry and ownership behavior.

#### Scenario: Full local transcript survives an exhausted sticky account

- **GIVEN** account A is selected for an unanchored prompt-cache-affine request
- **AND** the input contains a full self-contained transcript, response-owned reasoning state,
  retained assistant output, and fresh user input or a canonical scheduled-task heartbeat
- **AND** account B is eligible
- **WHEN** account A returns a quota rejection before any downstream event
- **THEN** the proxy removes response-owned reasoning state and item ids from the replay
- **AND** the proxy sends the account-neutral full resend on account B
- **AND** the response from account B is returned successfully

#### Scenario: HTTP and SSE quota rejection use the same recovery

- **WHEN** the pre-visible quota rejection arrives as either an HTTP error status or the first
  `response.failed` SSE event
- **THEN** the same account-neutral failover rules apply

#### Scenario: Scheduled heartbeat survives an exhausted sticky account

- **GIVEN** an unanchored full resend contains Codex host-generated scheduled-task heartbeats
- **AND** each heartbeat has the canonical `codex_app` automation shape and no upstream call id
- **WHEN** the selected account returns a pre-visible quota rejection
- **THEN** the proxy treats historical heartbeats as account-neutral host input
- **AND** the current heartbeat is retained as fresh input on the replay to another account
- **AND** malformed, namespaced differently, or call-id-bearing function outputs remain fail-closed

#### Scenario: Delta-shaped owner state stays fail-closed

- **GIVEN** an unanchored request whose input contains response-owned state and fresh user
  input but no retained prior assistant output
- **WHEN** the selected account returns a pre-visible quota rejection
- **THEN** the proxy surfaces the quota failure
- **AND** no part of the request is sent to another account

#### Scenario: Hard ownership stays fail-closed

- **GIVEN** a request carrying a previous-response, conversation, turn-state, or input-file
  owner, or constrained by single-account routing
- **WHEN** the owner returns a pre-visible quota rejection
- **THEN** the existing owner-bound behavior remains in force
- **AND** the proxy does not use this recovery to cross accounts
