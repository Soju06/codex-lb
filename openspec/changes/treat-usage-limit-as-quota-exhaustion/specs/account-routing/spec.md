## MODIFIED Requirements

### Requirement: Upstream rate and quota penalties are account-scoped by default

When upstream returns rate-limit or quota-exhaustion evidence for a selected account, the proxy MUST apply that penalty to the selected upstream account identity. The proxy MUST NOT invent model-scoped, transport-scoped, or request-kind-scoped upstream cooldown semantics unless upstream documentation or captured upstream response metadata proves that narrower upstream scope.

An upstream `rate_limit_exceeded` response MUST apply the account's rate-limit cooldown. An upstream `usage_limit_reached` response MUST apply quota-exhaustion health state so fresh usage from an unrelated available window cannot immediately reactivate the still-exhausted account. After the quota debounce expires, a fresh applicable long-window sample at 100% MUST preserve quota-exhausted state when no usable credit override exists; freshness alone is not recovery evidence. When that exhausted sample supplies its reset time, routing MUST use that observed long-window reset instead of an earlier fallback deadline. This health classification MUST NOT change pre-visible failover eligibility or the upstream error code surfaced to the client.

When an applicable exhausted sample omits reset metadata, an elapsed fallback deadline MUST NOT reactivate the account. Credit overrides of an explicit quota block MUST use credit evidence recorded strictly after the block; cached pre-block credit availability MUST NOT clear the persisted quota status or block markers on any replica.

#### Scenario: Upstream 429 marks only the selected account

- **GIVEN** account A is selected for a request
- **AND** upstream returns a rate-limit response for that request
- **WHEN** the proxy records the penalty
- **THEN** it marks account A as rate-limited or cooling down
- **AND** it does not create model-scoped or transport-scoped upstream cooldown buckets without upstream evidence

#### Scenario: Usage exhaustion uses quota recovery

- **GIVEN** account A is selected while another account remains usable
- **AND** upstream returns `usage_limit_reached` for account A
- **WHEN** the proxy records the penalty
- **THEN** it marks account A quota-exceeded and preserves pre-visible failover
- **AND** fresh usage from an unrelated available window does not immediately return account A to routing
- **AND** any surfaced failure preserves the upstream error code

#### Scenario: Fresh exhausted long-window usage does not recover quota state

- **GIVEN** account A was explicitly marked quota-exceeded by an upstream usage-limit response
- **AND** its quota debounce has expired
- **WHEN** refreshed usage still reports 100% consumption in the applicable long window with no usable credit override
- **THEN** account A remains quota-exceeded and unavailable for ordinary routing
- **AND** the observed long-window reset replaces any shorter fallback reset deadline

#### Scenario: Exhausted usage without reset metadata preserves the block

- **GIVEN** an explicitly quota-exhausted account has an elapsed fallback deadline
- **WHEN** its applicable long-window usage remains at 100% without reset metadata
- **THEN** foreground selection keeps the account unavailable
- **AND** later post-block usage proving available quota can recover the account

#### Scenario: Cached credits cannot override a new usage-limit failure

- **GIVEN** cached usage reports usable credits before an upstream usage-limit rejection
- **WHEN** another replica selects accounts after the rejection is persisted
- **THEN** the rejected account remains quota-exceeded and retains its block marker
- **AND** only credit evidence recorded after that block can override quota exhaustion
