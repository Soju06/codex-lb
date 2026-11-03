## ADDED Requirements

### Requirement: Stateless Responses quota failover preserves the request

When an eligible upstream account returns an explicit quota or usage-limit rejection before any output is visible downstream, the proxy SHALL retry the unchanged stateless Responses body on the next eligible account by default. The proxy MUST preserve every input item, item ordering, ciphertext, tool argument and result, inline agent message and metadata; it MUST NOT send a classification-only projection in their place. Inline unknown future item types and empty text parts MUST NOT on their own block migration.

The proxy MUST NOT migrate when the request has a previous-response, conversation or stored-prompt anchor; a known account-owned file, container, vector-store, hosted resource or MCP approval reference; a hard turn-state or file owner; or explicit single-account routing. Declared MCP tools and inline MCP and computer results MUST NOT on their own block migration. Authentication failures, invalid input errors and failures after downstream output MUST NOT enable this quota recovery. Each exhausted account MUST remain excluded. Key-scope, model and account eligibility, API-key reservation settlement and account-health ordering MUST remain in effect.

When a legacy hard session affinity resolves to an unavailable account before dispatch, the proxy MAY switch to soft affinity for a stateless request only if deterministic failover is enabled and no explicit account owner, single-account routing or other migration blocker applies. Following a pre-visible quota rejection, eligible stateless retry MUST also be able to leave a legacy hard session affinity.

For pre-visible quota rejections only, the permitted number of account attempts SHALL grow with the number of distinct exhausted accounts up to 64 or the existing deadline, stopping early when no eligible account remains. Non-quota failures MUST retain their existing attempt limits.

#### Scenario: Multiple accounts hit quota during a compacted turn
- **GIVEN** a stateless request contains encrypted compaction, encrypted reasoning and inline tool and agent history
- **WHEN** the first five selected accounts reject it for quota before output
- **THEN** the sixth eligible account receives the same request input
- **AND** the client receives its successful response without the internal quota errors

#### Scenario: The default permits previously unseen inline types
- **GIVEN** an unanchored request contains an unfamiliar inline item or an empty text part
- **WHEN** the serving account rejects it for quota before output
- **THEN** that item alone does not block a retry
- **AND** the original item is preserved unchanged

#### Scenario: Stored resources remain with their owner
- **GIVEN** a request contains a stored file, conversation, prompt, hosted resource or hard turn-state reference
- **WHEN** its owner reports quota exhaustion
- **THEN** this quota-recovery path does not send that owned reference to another account

#### Scenario: An unavailable legacy session owner does not block a stateless turn
- **GIVEN** a stateless request has a legacy hard session binding to account A
- **AND** account A is unavailable before the request is dispatched
- **WHEN** deterministic failover is enabled and account B is eligible
- **THEN** the proxy may select account B with soft affinity
- **AND** no account-owned reference or explicit owner is bypassed

#### Scenario: Inline tool data does not create account ownership
- **GIVEN** a stateless request declares an MCP tool or includes inline MCP or computer results
- **WHEN** the serving account rejects it for quota before output
- **THEN** those tool definitions and results alone do not block an unchanged retry

#### Scenario: No duplicate generation after visible output
- **GIVEN** the serving account has already sent visible response content
- **WHEN** that response later fails
- **THEN** this pre-visible quota-recovery path does not replay the turn

## MODIFIED Requirements

### Requirement: Account-bound retries remain on their dispatch owner

The proxy MUST bind a Responses request body that is not a canonical
account-neutral fresh replay to the account that first receives that exact
body. Every later selection for that request MUST treat the dispatch owner as a
strict required account across HTTP streaming, HTTP bridge, and direct
WebSocket transports, except for the pre-visible HTTP quota recovery specified
by the Stateless Responses quota failover requirement.

The proxy MUST NOT exclude the dispatch owner and send the retained body to a
different account during stale-anchor recovery, retryable account failure,
Trusted Access migration or degradation, bridge reconnect, or WebSocket account
switching, except for the eligible HTTP quota recovery specified above. If the
required owner is unavailable and no specified recovery applies, the proxy MUST
fail closed without dispatching the retained body to another account.

The proxy MAY perform one forced authentication refresh and replay a retained
account-bound body on the same dispatch owner. It MUST NOT use that refresh to
exclude the owner or migrate the body to another account, and a permanent
authentication failure MUST remain terminal for the bound body.

The proxy MAY clear the dispatch-owner binding only after verified recovery
replaces the exact wire body and the replacement passes the canonical
account-neutral-fresh-replay predicate, or a pre-visible quota rejection permits replay of the identical stateless body under the quota-failover requirement below. Removing `previous_response_id` alone
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
- **AND** the retry fails closed when account A is unavailable unless the unchanged body is eligible under the stateless quota-failover requirement

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
