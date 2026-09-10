## ADDED Requirements

### Requirement: Completed operator probe recovery

The existing account probe endpoint MUST clear a persisted rate-limit or quota hold only after a valid `response.completed` for the held account and recorded rejected model and service tier. HTTP success without completed execution MUST NOT authorize recovery. Unknown historical rejection scope MUST NOT authorize recovery, including when a default probe model succeeds.

Every persisted rejection MUST advance a durable monotonic block generation, including repeated rejections with identical integer-second block timestamps. Recovery MUST compare the generation captured before dispatch, account status, block markers and credential identity. A newer rejection, pause, deletion or reauthentication MUST win. Concurrent probes for the same hold MUST share a bounded admission. Probe failure, timeout, cancellation, malformed stream and incomplete execution MUST NOT authorize completed-probe recovery. Existing ordinary usage and deadline recovery remain independent.

Recovery MUST preserve routing policy, model eligibility, file ownership, registered continuation ownership and existing account affinity. Ordinary quota refresh and deadline recovery rules MUST remain unchanged. No automatic provider probe SHALL be introduced.

#### Scenario: Successful recovery after restart

- **GIVEN** a held account with recorded rejected scope and no replica-local block state
- **WHEN** an operator probe completes successfully for that same scope and its generation remains current
- **THEN** the persisted hold is cleared and fresh eligible requests can select the account
- **AND** established owner bindings remain unchanged

#### Scenario: Same-second rejection wins

- **GIVEN** an operator probe captured a hold generation
- **WHEN** a newer upstream rejection persists with the same reset and block timestamps before probe settlement
- **THEN** its advanced generation prevents recovery

#### Scenario: Historical hold remains protected

- **GIVEN** a hold migrated without trustworthy rejected scope
- **WHEN** a probe completes for any model
- **THEN** completed-probe recovery does not clear that hold

#### Scenario: Incomplete provider response

- **GIVEN** a held account whose scope matches the probe
- **WHEN** upstream returns HTTP 200 followed by failure, incomplete execution or EOF without completion
- **THEN** the persisted hold remains unchanged

## MODIFIED Requirements

### Requirement: Upstream rate and quota penalties are account-scoped by default

When upstream returns rate-limit or quota-exhaustion evidence for a selected account, the proxy MUST apply that penalty to the selected upstream account identity. The proxy MUST NOT invent model-scoped, transport-scoped, or request-kind-scoped upstream cooldown semantics unless upstream documentation or captured upstream response metadata proves that narrower upstream scope.

Upstream `rate_limit_exceeded` and `usage_limit_reached` responses MUST retain the account's rate-limit classification and persisted reset deadline. Early recovery from usage evidence MUST require available quota, not freshness alone: the primary sample MUST report less than 100% usage, or its reset MUST have elapsed with a newer available long-window sample. An applicable exhausted long-window sample MUST NOT clear the rate-limit hold. Peer replicas without runtime evidence of the current block MUST continue to honor the persisted deadline unless the completed operator probe recovery contract authorizes a guarded update. These recovery rules MUST NOT change pre-visible failover eligibility or the upstream error code surfaced to the client.

After the quota debounce expires, a fresh applicable long-window sample at 100% MUST preserve an explicit quota-exhausted state when no usable credit override exists. When that exhausted sample supplies its reset time, routing MUST use that observed long-window reset instead of an earlier fallback deadline.

The evidence gate MUST apply only to usage-based recovery of rate-limit and explicit quota-exhaustion states, not unrelated account-health penalties. Ordinary `rate_limit_exceeded` cooldown and persisted-deadline expiry during foreground selection MUST remain unchanged and MUST NOT require a new quota sample. Monthly usage unsupported by the account's plan MUST NOT block recovery based on an available post-block primary sample.

When a fresh applicable exhausted sample omits reset metadata, an elapsed fallback deadline MUST NOT reactivate the account. An exhausted sample MUST be recent and, when a block marker exists, unambiguously post-block before replacing or removing that block's reset deadline. Credit overrides of an explicit quota block MUST use credit evidence recorded strictly after the block; cached pre-block credit availability MUST NOT clear the persisted quota status or block markers on any replica. Evidence recorded in the same integer Unix second as the persisted block MUST NOT count as post-block evidence. An expired long-window row MUST NOT veto recovery based on an available post-block primary sample or itself serve as fresh availability evidence.

#### Scenario: Upstream 429 marks only the selected account

- **GIVEN** account A is selected for a request
- **AND** upstream returns a rate-limit response for that request
- **WHEN** the proxy records the penalty
- **THEN** it marks account A as rate-limited or cooling down
- **AND** it does not create model-scoped or transport-scoped upstream cooldown buckets without upstream evidence

#### Scenario: Usage exhaustion preserves rate-limit deadlines

- **GIVEN** account A is selected while another account remains usable
- **AND** upstream returns `usage_limit_reached` for account A
- **WHEN** the proxy records the penalty
- **THEN** it marks account A rate-limited and preserves pre-visible failover
- **AND** fresh usage that still reports an exhausted primary or applicable long window does not clear the persisted deadline on either the marking replica or a peer
- **AND** any surfaced failure preserves the upstream error code

#### Scenario: Available usage permits early recovery on the marking replica

- **GIVEN** an upstream rate-limit hold with a future reset deadline and elapsed local cooldown
- **WHEN** a post-block primary sample reports less than 100% usage and no applicable long-window sample reports exhaustion
- **THEN** the marking replica can recover the account through the existing persisted state transition
- **AND** peer replicas observe the recovered state

#### Scenario: Unsupported monthly usage does not veto background recovery

- **GIVEN** an account whose plan has no monthly quota and whose persisted rate-limit deadline has elapsed
- **AND** storage contains an exhausted monthly row and an available post-block primary row
- **WHEN** background recovery evaluates the account
- **THEN** it ignores the unsupported monthly row and permits recovery from the primary evidence

#### Scenario: Ordinary rate-limit cooldown expires without usage refresh

- **GIVEN** an account blocked by `rate_limit_exceeded` with a persisted reset deadline
- **WHEN** foreground selection runs after that deadline without new usage data
- **THEN** the existing cooldown-expiry path can recover the account without requiring quota evidence

#### Scenario: Fresh exhausted long-window usage does not recover quota state

- **GIVEN** account A was explicitly marked quota-exceeded by an upstream quota rejection
- **AND** its quota debounce has expired
- **WHEN** refreshed usage still reports 100% consumption in the applicable long window with no usable credit override
- **THEN** account A remains quota-exceeded and unavailable for ordinary routing
- **AND** the observed long-window reset replaces any shorter fallback reset deadline

#### Scenario: Exhausted usage without reset metadata preserves the block

- **GIVEN** an explicitly quota-exhausted account has an elapsed fallback deadline
- **WHEN** its applicable long-window usage remains at 100% without reset metadata
- **THEN** foreground selection keeps the account unavailable
- **AND** later post-block usage proving available quota can recover the account

#### Scenario: Cached credits cannot override a new quota failure

- **GIVEN** cached usage reports usable credits before an upstream quota rejection
- **WHEN** another replica selects accounts after the rejection is persisted
- **THEN** the rejected account remains quota-exceeded and retains its block marker
- **AND** only credit evidence recorded after that block can override quota exhaustion

#### Scenario: Fractional pre-block credits cannot clear a persisted quota block

- **GIVEN** a credit snapshot was recorded before a rejection within the same Unix second
- **WHEN** the rejection is persisted with integer-second precision and either replica evaluates recovery
- **THEN** the snapshot does not qualify as post-block evidence
- **AND** a new credit snapshot in a later second can recover the account

#### Scenario: Expired exhaustion does not veto available primary evidence

- **GIVEN** a marking replica has an elapsed local cooldown and a future persisted rate-limit deadline
- **AND** a fresh post-block primary sample reports available quota while a stored exhausted long-window row has expired
- **WHEN** the replica evaluates early recovery
- **THEN** it ignores the expired long-window row and recovers from the primary evidence

#### Scenario: Historical exhaustion cannot rewrite a newer quota deadline

- **GIVEN** an explicit quota rejection has a persisted fallback deadline
- **AND** the latest exhausted long-window row is stale or not unambiguously post-block
- **WHEN** routing evaluates the account
- **THEN** that row does not replace or remove the persisted fallback deadline
