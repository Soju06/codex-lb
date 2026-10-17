# account-routing Specification

## Purpose
Defines how the proxy chooses which account serves a request and how upstream feedback changes that choice. It covers the selection strategies operators can pick (relative availability, sequential and reset drain, single-account, manual and additional-quota policies, reset-window preference), how rate-limit, overload, and error signals scope penalties to the responsible account, and which of those signals must be shared across replicas versus kept replica-local. The goal is to spend pooled quota deliberately while never leaving a request routed to an account that cannot serve it.
## Requirements
### Requirement: Relative availability routing

The proxy account selector SHALL support a `relative_availability` routing strategy. The strategy SHALL evaluate only accounts that have passed the existing eligibility, health-tier, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates. Re-authentication-required accounts SHALL be treated as hard-blocked routing candidates, the same as paused and deactivated accounts. For each candidate, it SHALL compute a raw score from remaining secondary-window credits divided by seconds until the secondary-window reset, using bounded fallbacks for unknown or near-immediate reset times, and SHALL select from the highest weighted candidates according to the configured power and top-K cutoff.

#### Scenario: Soon-resetting usable credits are preferred
- **GIVEN** two healthy eligible accounts with equal remaining secondary credits
- **AND** one account's secondary window resets sooner
- **WHEN** account selection uses `relative_availability`
- **THEN** the sooner-resetting account receives the higher relative-availability score

#### Scenario: Relative availability preserves canonical gates
- **GIVEN** one account is paused, reauth-required, deactivated, rate-limited, quota-exceeded, cooling down, or outside the requested model plan
- **WHEN** account selection uses `relative_availability`
- **THEN** that account is not selected by the relative-availability strategy

### Requirement: Relative availability dashboard tuning
Dashboard settings SHALL expose `relative_availability_power` and `relative_availability_top_k` alongside the routing strategy. The backend SHALL validate power as positive and top-K as an integer from 1 through 20. The dashboard UI SHALL reject non-integer top-K input without truncating decimal values.

#### Scenario: Sticky fallback uses configured tuning
- **GIVEN** a sticky request has no usable pinned account
- **AND** relative-availability routing is enabled with non-default power or top-K settings
- **WHEN** the load balancer falls back to fresh selection
- **THEN** it applies the configured relative-availability power and top-K values

#### Scenario: Decimal top-K input is rejected
- **WHEN** an operator enters `1.5` for relative availability top-K
- **THEN** the dashboard does not enable saving that value as `1`

### Requirement: Relative availability logs avoid raw account emails
Relative-availability selection diagnostics SHALL identify accounts using stable internal account IDs or another non-PII identifier. They SHALL NOT emit raw account emails in candidate, top-K, winner, or hot-path selected-account logs.

#### Scenario: Candidate logs use account IDs
- **WHEN** relative-availability routing logs candidate or winner diagnostics
- **THEN** the log message includes the candidate account ID
- **AND** the log message does not include the account email address

### Requirement: Sequential drain routing
The proxy account selector SHALL support a `sequential_drain` routing strategy. The strategy SHALL evaluate only accounts that pass the existing eligibility, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates, then select the usable account with the lowest effective secondary capacity before moving to higher-capacity accounts.

#### Scenario: Lowest-capacity usable account is drained first
- **GIVEN** multiple healthy eligible accounts with different effective secondary capacities
- **WHEN** account selection uses `sequential_drain`
- **THEN** the account with the lowest effective secondary capacity is selected

#### Scenario: Exhausted lower-capacity accounts are skipped
- **GIVEN** the lowest-capacity account has no usable quota
- **WHEN** account selection uses `sequential_drain`
- **THEN** the selector chooses the next-lowest usable capacity account

### Requirement: Reset drain routing
The proxy account selector SHALL support a `reset_drain` routing strategy. The strategy SHALL evaluate only accounts that pass the existing eligibility, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates, then prefer usable accounts whose secondary quota reset is nearest. When secondary reset data is unavailable, it SHALL fall back to the primary reset time. Within the same reset bucket, it SHALL prefer the account with more remaining usable quota.

#### Scenario: Soonest resetting usable account is selected
- **GIVEN** multiple healthy eligible accounts with usable quota
- **AND** their secondary quota windows reset at different times
- **WHEN** account selection uses `reset_drain`
- **THEN** the usable account with the nearest secondary reset is selected

#### Scenario: Same-reset accounts drain higher remaining quota first
- **GIVEN** multiple healthy eligible accounts in the same reset bucket
- **WHEN** account selection uses `reset_drain`
- **THEN** the account with more remaining usable quota is selected

### Requirement: Single-account routing
The proxy routing layer SHALL support a `single_account` routing strategy configured by `single_account_id`. When enabled, the proxy SHALL route only through the configured account if that account exists, is available, and matches the requested model-plan scope. If the setting is missing, unavailable, or incompatible with the request, the proxy SHALL fail the request with a routing error instead of silently falling back to another account.

#### Scenario: Configured account serves matching traffic
- **GIVEN** `single_account` routing is enabled with a configured available account
- **AND** the account matches the requested model-plan scope
- **WHEN** the proxy selects an account
- **THEN** the configured account is selected

#### Scenario: Missing or unavailable selected account does not fall back
- **GIVEN** `single_account` routing is enabled
- **AND** the configured account is missing, unavailable, exhausted, or outside the requested model-plan scope
- **WHEN** the proxy selects an account
- **THEN** no alternate account is selected
- **AND** the request fails with a routing error

### Requirement: Drain routing dashboard settings
Dashboard settings SHALL expose `sequential_drain`, `reset_drain`, and `single_account` as valid routing strategies. When `single_account` is selected, the dashboard SHALL allow choosing the configured account id and the backend SHALL persist it as nullable `single_account_id`.

#### Scenario: Operator saves a single-account route
- **WHEN** an operator selects `single_account` and chooses an account
- **THEN** the settings API persists the selected account id
- **AND** subsequent settings responses include that id

### Requirement: Manual account routing policy

Each account SHALL have a persisted manual routing policy with one of `normal`, `burn_first`, or `preserve`. Missing or legacy values SHALL be treated as `normal`.

#### Scenario: expendable accounts are selected before normal accounts

- **GIVEN** at least one eligible account has routing policy `burn_first`
- **AND** at least one eligible account has routing policy `normal`
- **WHEN** the load balancer selects an account
- **THEN** it selects from the `burn_first` pool before considering `normal` accounts

#### Scenario: preserved accounts are fallback only

- **GIVEN** at least one eligible account has routing policy `normal`
- **AND** at least one eligible account has routing policy `preserve`
- **WHEN** the load balancer selects an account
- **THEN** it selects from the `normal` pool before considering `preserve` accounts

#### Scenario: routing policy does not bypass eligibility gates

- **GIVEN** a request is filtered by model plan or additional quota eligibility
- **WHEN** an account has routing policy `burn_first`
- **THEN** that account is still excluded if it fails the model plan or additional quota gate

### Requirement: Additional quota routing policy

Each known additional quota MAY have a routing policy of `inherit`, `normal`, `burn_first`, or `preserve`. `inherit` SHALL use the selected account's routing policy. The other values SHALL override account routing policy for requests gated by that additional quota.

For additional-quota-gated requests, account selection SHALL use fresh additional-quota usage windows for budget and reset comparison and SHALL NOT reject an account solely because its standard 5h or 7d quota is exhausted.

#### Scenario: additional quota inherits account policy

- **GIVEN** an additional quota has routing policy `inherit`
- **WHEN** the load balancer selects an account for that additional quota
- **THEN** it applies the account's own routing policy

#### Scenario: additional quota override takes precedence

- **GIVEN** an additional quota has routing policy `burn_first`
- **AND** an account with fresh available quota for that additional quota has standard Codex quota exhausted
- **WHEN** the load balancer selects an account for that additional quota
- **THEN** the account remains eligible and is treated as `burn_first` for that selection

### Requirement: Reset-window preference selection
When earlier-reset routing preference is enabled, the account selector SHALL
support choosing which quota window drives reset-time ordering. The supported
windows SHALL be `primary` and `secondary`. The default SHALL be `secondary` to
preserve existing behavior.

#### Scenario: Primary reset window is selected
- **GIVEN** two healthy eligible accounts with different primary reset times
- **AND** earlier-reset preference is enabled with reset window `primary`
- **WHEN** account selection evaluates otherwise comparable candidates
- **THEN** the account with the earlier primary reset is preferred

#### Scenario: Secondary reset window remains the default
- **GIVEN** earlier-reset preference is enabled without an explicit reset-window override
- **WHEN** account selection evaluates otherwise comparable candidates
- **THEN** the account selector uses secondary-window reset ordering

### Requirement: Reset-window preference propagation
All proxy account-selection surfaces SHALL pass the configured reset-window
preference into the canonical load balancer. This includes HTTP responses,
WebSocket responses, bridge requests, compact requests, transcription requests,
file-backed responses, Codex control requests, and sticky fallback selection.

#### Scenario: WebSocket selection uses the configured window
- **GIVEN** dashboard settings set the reset-window preference to `primary`
- **WHEN** a WebSocket response request selects an account
- **THEN** the load balancer receives `primary` as the reset-window preference

### Requirement: Foreground routing treats local usage snapshots as non-authoritative

Foreground proxy account selection MUST NOT reject an otherwise active account solely because local standard usage snapshots, synthetic planner costs, or inferred budget pressure report that the account has reached or exceeded 100 percent usage. Such local usage data MAY influence ranking, health/drain decisions, opportunistic burn policy, dashboards, and diagnostics, but it MUST NOT be reported as upstream rate limiting and MUST NOT produce `no_accounts` before an upstream attempt when no explicit local policy or local capacity guard is exhausted.

#### Scenario: Active account at local primary usage exhaustion is still selectable

- **GIVEN** an upstream account is persisted as active
- **AND** its latest local primary usage snapshot reports 100 percent usage with a future reset
- **WHEN** foreground account selection evaluates the account
- **THEN** the account remains eligible for upstream routing
- **AND** the selection result does not report a local `Rate limit exceeded` or `no_accounts` failure

#### Scenario: Active account at local secondary usage exhaustion is still selectable

- **GIVEN** an upstream account is persisted as active
- **AND** its latest local secondary usage snapshot reports 100 percent usage with a future reset
- **WHEN** foreground account selection evaluates the account
- **THEN** the account remains eligible for upstream routing
- **AND** the local secondary usage snapshot is not promoted into a persisted upstream quota-exceeded state before an upstream response proves quota exhaustion

#### Scenario: Advisory usage reset is not persisted as an account block

- **GIVEN** an upstream account is persisted as active
- **AND** its latest local usage snapshot reports 100 percent usage with a future reset
- **WHEN** foreground account selection evaluates and persists selection state for the active account
- **THEN** the account-level blocking reset remains unset
- **AND** a later upstream rate-limit response without reset metadata is governed by upstream retry/backoff cooldown rather than the advisory usage reset

### Requirement: Upstream rate and quota penalties are account-scoped by default

When upstream returns rate-limit or quota-exhaustion evidence for a selected account, the proxy MUST apply that penalty to the selected upstream account identity. The proxy MUST NOT invent model-scoped, transport-scoped, or request-kind-scoped upstream cooldown semantics unless upstream documentation or captured upstream response metadata proves that narrower upstream scope.

Upstream `rate_limit_exceeded` and `usage_limit_reached` responses MUST retain the account's rate-limit classification and persisted reset deadline. Early recovery from usage evidence MUST require available quota, not freshness alone: the primary sample MUST report less than 100% usage, or its reset MUST have elapsed with a newer available long-window sample. An applicable exhausted long-window sample MUST NOT clear the rate-limit hold. Peer replicas without runtime evidence of the current block MUST continue to honor the persisted deadline. These recovery rules MUST NOT change pre-visible failover eligibility or the upstream error code surfaced to the client.

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

### Requirement: Upstream rejections of the request payload are account neutral

When upstream rejects a request because of the request payload itself, the proxy MUST NOT mutate the selected account's health: it MUST NOT record a transient account error, a rate-limit penalty, a quota penalty, or a permanent failure for that account. An upstream failure qualifies as a payload rejection only when it would reproduce identically on every account. The proxy MUST decide membership from the classified upstream message, never from the `invalid_request_error` code alone, and MUST require the upstream HTTP status to be 400 whenever a status is known. An upstream missing-tool-output rejection — the `invalid_request_error` whose message identifies a tool call with no matching tool output — MUST qualify. The proxy MUST also leave account health untouched for the model-entitlement rejection `The '<model>' model is not supported when using Codex with a ChatGPT account.`: that rejection is scoped to the named model and is not evidence about the account's ability to serve the models it is entitled to. Because upstream delivers that rejection on the streaming path with neither an error `code` nor an error `type`, which normalizes to the `upstream_error` fallback, the proxy MUST decide it from the message and the 400 status alone and MUST NOT require a particular normalized error code. Skipping the penalty MUST be logged so the decision is observable, and MUST NOT change the failure classification, the failover decision, or the status and body returned to the client.

#### Scenario: Missing-tool-output rejection leaves account health untouched

- **GIVEN** account A is selected for a request whose input references a tool call with no matching tool output
- **WHEN** upstream returns HTTP 400 `invalid_request_error` with a missing-tool-output message
- **THEN** the proxy does not increment account A's transient error count and does not mark it rate-limited, quota-exceeded, or permanently failed
- **AND** the failure is still classified `non_retryable` and surfaced to the client unchanged

#### Scenario: Repeated client payload rejection cannot starve unrelated sessions

- **GIVEN** one client repeatedly re-sends the same payload that upstream rejects for a missing tool output
- **WHEN** those requests are served by accounts shared with other sessions
- **THEN** no serving account enters error backoff because of that payload
- **AND** a session hard-pinned to one of those accounts is not failed with a saturated-hard-affinity selection error caused by that payload

#### Scenario: Model-entitlement rejection leaves account health untouched

- **GIVEN** account A is selected for a model it is not entitled to use
- **WHEN** upstream returns HTTP 400 stating the model is not supported when using Codex with a ChatGPT account, with the error code normalized to `upstream_error` or to `invalid_request_error`
- **THEN** the proxy does not increment account A's transient error count and does not mark it rate-limited, quota-exceeded, or permanently failed
- **AND** the skip is logged

#### Scenario: Model-entitlement rejection still fails over

- **GIVEN** account A returned the model-entitlement rejection for the requested model
- **WHEN** the proxy classifies that failure
- **THEN** the classification and failover decision are unchanged, so an account with a different entitlement is still attempted
- **AND** the status and body returned to the client when every attempt is exhausted are unchanged

#### Scenario: A model no source can serve cannot poison subscription accounts

- **GIVEN** a model that resolves to no enabled model source and therefore reaches subscription account selection
- **WHEN** a client polls that model repeatedly and every subscription account returns the model-entitlement rejection
- **THEN** no serving account enters error backoff because of those rejections
- **AND** unrelated traffic hard-pinned to those accounts is not denied with a continuity-owner-unavailable or no-available-accounts selection error caused by them

#### Scenario: Model-entitlement rejection still penalizes the account

- **GIVEN** account A is selected for a request
- **WHEN** upstream fails with a non-400 status whose message matches the model-entitlement rejection
- **THEN** the proxy records the account-health penalty for account A as before, because only a genuine HTTP 400 qualifies as the model-scoped rejection

#### Scenario: A genuine upstream failure still penalizes the account

- **GIVEN** account A is selected for a request
- **WHEN** upstream fails with an `upstream_error` whose message is not the model-entitlement rejection
- **THEN** the proxy records the account-health penalty for account A as before

### Requirement: Stale in-memory account sessions must not stay routable

The service MUST remove accounts from routing when they are paused, deleted,
marked `reauth_required`, or otherwise made unavailable by a permanent
credential/session failure. This applies even when a long-lived in-memory HTTP
bridge session still holds an older `ACTIVE` account object. When the account
is successfully imported, re-authenticated, or reactivated, the service MUST
clear the in-memory unavailable marker. The routing-unavailable state MUST be
derived from persisted account status and MUST converge on every replica
within the cache-invalidation bus bound (marks and clears both propagate);
bridge-session reuse checks MUST NOT add per-request database reads; sessions
pinned to a deleted account MUST NOT be reused on any replica. A local
unavailable mark set while a snapshot refresh is in flight MUST survive that
refresh: a refresh MUST NOT clear marks it could not have observed as committed
status when its database read started.

#### Scenario: Stale bridge session is not reused after account becomes unavailable

- **GIVEN** an HTTP bridge session was created while account A was active
- **AND** account A is later marked unavailable for routing
- **WHEN** a subsequent bridge request looks for a reusable session
- **THEN** the stale session for account A is not reused

#### Scenario: Re-authentication clears routing-unavailable state

- **GIVEN** account A was marked unavailable after a credential/session failure,
  including on a replica other than the one handling the re-authentication
- **WHEN** account A is re-authenticated successfully
- **THEN** account A is eligible for routing again subject to normal account
  selection gates on every replica after the invalidation bus converges,
  without requiring a process restart

#### Scenario: Pause on one replica stops bridge-session reuse on peers

- **GIVEN** replica B holds a warm HTTP bridge session pinned to account A whose in-memory snapshot reads `ACTIVE`
- **WHEN** account A is paused via a request served by replica A
- **THEN** after the invalidation bus converges, replica B refuses to reuse the warm bridge session for account A

#### Scenario: Local mark set during an in-flight snapshot refresh is preserved

- **GIVEN** a routing snapshot refresh is in flight and its database read observed
  account A as `ACTIVE` before a permanent failure was committed
- **WHEN** account A is marked routing-unavailable locally before that refresh
  finishes
- **THEN** the completed refresh MUST NOT drop the local mark based on its stale
  snapshot, and account A remains routing-unavailable on that replica until a
  later refresh observes a committed routable status

#### Scenario: Deletion on one replica stops bridge-session reuse on peers

- **GIVEN** replica B holds a warm HTTP bridge session pinned to account A whose in-memory snapshot reads `ACTIVE`
- **WHEN** account A is deleted via a request served by replica A
- **THEN** after the invalidation bus converges, replica B treats account A as routing-unavailable even though its in-memory account object still reads `ACTIVE`

### Requirement: Upstream rate-limit cooldown honors the Retry-After hint duration

The account cooldown SHALL last for the full duration expressed by a "try
again in" hint on an upstream rate-limit error. The parser SHALL
recognize hour, minute, second, and millisecond units, including their word
forms, and SHALL sum compound hints such as `1h2m3s` into a single duration.
A unit token SHALL be recognized only when it is not immediately followed by
another letter, so an unsupported longer word whose prefix matches a unit (for
example `month`, where `m` prefixes the word) is not mis-read as that shorter
unit. When the hint contains no recognizable unit token, the system SHALL fall
back to the error-count backoff schedule. A rate-limited account SHALL NOT be
re-selected before its cooldown elapses.

Explicit upstream reset metadata SHALL be accepted only when it resolves to a
finite deadline strictly later than the current time and no more than
`RATE_LIMIT_RESET_MAX_HORIZON_SECONDS` (366 days) in the future. `resets_at`
SHALL be interpreted as an absolute Unix timestamp and `resets_in_seconds`
SHALL be interpreted as a relative duration. When `resets_at` is invalid but
`resets_in_seconds` is valid, the relative duration SHALL be used. An accepted
fractional deadline SHALL be rounded up to the next whole second before
persistence. A persisted integer deadline produced by that rounding MAY be
less than one second beyond the raw 366-day horizon and MUST remain valid when
selection reconstructs it. When neither field is valid, the error SHALL be
treated as carrying no explicit reset metadata.

When the upstream rate-limit error carries no valid explicit reset metadata,
the resolved cooldown deadline SHALL be persisted on the account row
(`reset_at`) so the cooldown survives process restarts and is visible to all
replicas sharing the database: a parsed Retry-After hint deadline SHALL be
persisted rounded up to the next whole second (persistence stores `reset_at`
as an integer, so a short or fractional hint MUST NOT truncate down to an
already-elapsed deadline), and when the cooldown comes from the error-count
backoff fallback the persisted deadline SHALL be at least
`RATE_LIMITED_MIN_COOLDOWN_SECONDS` (30 seconds) in the future. The marking
replica's in-process cooldown MAY remain shorter than the persisted deadline
so its existing fresh-usage recovery gate is unchanged.

An already-persisted `rate_limited` reset deadline beyond the same plausibility
horizon SHALL be treated as missing metadata rather than as an unexpired
cooldown. A row carrying `blocked_at` SHALL still honor the existing 30-second
minimum floor and SHALL require recent usage evidence recorded after that block
before selection-time recovery may clear it. A row without `blocked_at` SHALL
require recent available usage evidence. In both cases, every applicable
derived quota window MUST report below `100%` usage before recovery.

#### Scenario: Compound minute-and-second hint sets the full cooldown

- **GIVEN** an upstream 429 whose message says "try again in 6m0s"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 360 seconds
- **AND** the account is not re-selected until its cooldown elapses

#### Scenario: Minutes-only hint is honored

- **GIVEN** an upstream 429 whose message says "try again in 20m"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 1200 seconds

#### Scenario: Unparseable hint falls back to backoff

- **GIVEN** an upstream 429 whose message has no recognizable "try again in" duration
- **WHEN** the balancer records the rate limit for the account
- **THEN** the cooldown uses the error-count backoff schedule instead

#### Scenario: Unsupported longer word is not mis-read as a shorter unit

- **GIVEN** an upstream 429 whose message says "try again in 1 month"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the `month` token is not read as a 1-minute hint
- **AND** the cooldown uses the error-count backoff schedule instead

#### Scenario: Cooldown without upstream reset metadata is persisted

- **GIVEN** an upstream 429 that carries no `resets_at`/`resets_in_seconds` metadata and no parseable Retry-After hint
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted account row holds status `RATE_LIMITED` with `blocked_at` set
- **AND** the persisted `reset_at` is at least 30 seconds in the future

#### Scenario: Retry-After hint deadline is persisted

- **GIVEN** an upstream 429 whose message says "try again in 20m" and that carries no reset metadata
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted `reset_at` is approximately 1200 seconds in the future

#### Scenario: Short fractional Retry-After hint is not truncated away

- **GIVEN** an upstream 429 whose message says "try again in 500ms" and that carries no reset metadata
- **WHEN** the balancer records the rate limit for the account
- **THEN** the persisted integer `reset_at` deadline is strictly in the future
- **AND** peer replicas honor the hinted cooldown instead of reselecting the account immediately

#### Scenario: Plausible explicit reset metadata remains authoritative

- **GIVEN** an OpenAI service 429 carrying a finite `resets_at` deadline 30 days in the future
- **WHEN** the balancer records the rate limit for the account
- **THEN** the accepted explicit deadline is persisted
- **AND** the Retry-After/backoff fallback does not replace it

#### Scenario: Implausible explicit reset metadata uses the bounded fallback

- **GIVEN** an OpenAI service 429 carrying `resets_at=15023672358` while the current Unix time is approximately `1784146959`
- **AND** the error carries no valid `resets_in_seconds` or parseable duration
- **WHEN** the balancer records the rate limit for the account
- **THEN** the implausible absolute deadline is rejected
- **AND** the persisted deadline uses the minimum bounded backoff instead

#### Scenario: Valid relative metadata survives an invalid absolute value

- **GIVEN** an OpenAI service 429 whose `resets_at` is implausibly far in the future
- **AND** whose `resets_in_seconds` is a finite positive duration within 366 days
- **WHEN** the balancer records the rate limit for the account
- **THEN** the relative duration determines the persisted deadline

#### Scenario: Horizon-edge rounding remains stable

- **GIVEN** valid absolute or relative reset metadata resolves exactly 366 days after a fractional current timestamp
- **WHEN** the balancer rounds and persists the deadline to a whole second
- **THEN** persisted-state reconstruction continues to accept that deadline
- **AND** does not clear the cooldown solely because rounding crossed the raw horizon by less than one second

#### Scenario: Existing implausible deadline does not pin selection indefinitely

- **GIVEN** a persisted `rate_limited` account whose `reset_at` is more than 366 days in the future
- **AND** whose `blocked_at` minimum floor has elapsed
- **WHEN** selection reconstructs the account from fresh available usage evidence
- **THEN** the implausible deadline is treated as missing metadata
- **AND** normal compare-and-set recovery may restore the account to `active`

#### Scenario: Exhausted long-window quota prevents poisoned-row recovery

- **GIVEN** a persisted `rate_limited` account whose reset deadline is implausible
- **AND** a fresh primary window reports available quota
- **AND** an applicable weekly or monthly window reports `100%` usage
- **WHEN** selection reconstructs the account
- **THEN** the account remains `rate_limited`

#### Scenario: Implausible legacy deadline without a block marker recovers

- **GIVEN** a persisted `rate_limited` account whose reset deadline is implausible
- **AND** the row has no `blocked_at` marker
- **WHEN** selection reconstructs the account from recent available usage in every applicable window
- **THEN** normal compare-and-set recovery may restore the account to `active`

### Requirement: Selection state expires elapsed usage windows

When building account selection state, the proxy SHALL treat any main-window usage sample (primary or secondary) whose `reset_at` timestamp has elapsed as a reset window: the derived used percentage becomes `0.0` and the derived reset timestamp is cleared, regardless of the sample's recorded used percentage. The rule SHALL apply after weekly-only primary remapping and SHALL mutate only derived selection inputs, not stored usage rows. Expired samples SHALL map to `0.0` rather than unknown so usage-derived status recovery still evaluates.

#### Scenario: Stale sub-100% primary sample stops gating selection

- **GIVEN** upstream stopped reporting a primary window for an account
- **AND** the account's last stored primary row reports 87% used with an elapsed `reset_at`
- **WHEN** selection state is built for that account
- **THEN** the derived primary usage is `0.0` with no reset timestamp
- **AND** the sample no longer holds the account in the soft-drain tier or above sticky budget-safety thresholds

#### Scenario: Expired sample still allows blocked-status recovery

- **GIVEN** an account persisted as `rate_limited` whose usage sample has an elapsed `reset_at`
- **WHEN** selection state is built for that account
- **THEN** the expired sample evaluates as `0.0` used rather than unknown
- **AND** usage-derived status recovery can still return the account to `active`

#### Scenario: Weekly-only remap happens before expiry

- **GIVEN** an account whose payload reports only a weekly window in the primary slot
- **WHEN** selection state is built
- **THEN** the weekly-primary remap into the secondary slot is evaluated on the raw samples
- **AND** the elapsed-reset expiry applies to the remapped derived values

### Requirement: Rate-limit cooldowns are enforced across replicas

A replica that did not observe the upstream 429 MUST NOT transition a `RATE_LIMITED` account to `ACTIVE` while the persisted `reset_at` deadline is in the future unless background usage refresh proves that the exact blocked Free monthly window reset under the strict exception below. For `RATE_LIMITED` rows with `blocked_at` set but no persisted `reset_at` (legacy rows written before cooldown persistence), replicas MUST hold the account `RATE_LIMITED` until at least `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`. Recovery transitions MUST be written through the compare-and-set status update (`update_status_if_current`) so a stale snapshot cannot clobber a newer marking.

The reset-confirmed exception SHALL apply only to a Free account with a still-future persisted deadline after the 30-second minimum floor has elapsed. Post-block monthly history MUST contain a baseline whose reset deadline matches the persisted account deadline within five seconds, and an adjacent monthly before/after pair at or after that baseline MUST prove a real temporal reset. Both the after sample and latest monthly sample MUST be post-block and below `100%`. The recovery compare-and-set MUST match the persisted status, deactivation reason, `reset_at`, and `blocked_at`, then clear both markers when it writes `ACTIVE`. The evidence MAY be loaded from persisted history after a process restart, but availability alone and comparisons between non-neighboring rows MUST NOT satisfy the exception.

This constraint applies to every recovery path that writes account status, including the usage-refresh reconcile path. A usage refresh that observes available quota for a `RATE_LIMITED` account with `blocked_at` set MUST NOT rewrite the account to `ACTIVE` or clear its markers while the effective persisted cooldown is running unless the strict reset-confirmed exception succeeds. The replica that observed the current 429 MAY still recover earlier through its runtime-cooldown-gated fresh-usage path only when its runtime block marker is at least as recent as the effective persisted `blocked_at`; leftover runtime state from an earlier 429 MUST NOT unlock early recovery of a newer block. `RATE_LIMITED` rows without `blocked_at` keep the existing fresh-usage recovery. Generic 429 and Retry-After cooldowns without matching reset evidence, reset timestamp jitter, exhausted post-reset windows, and non-Free account exhaustion MUST remain protected until their ordinary recovery condition is met.

#### Scenario: Usage refresh does not clear a running Retry-After cooldown

- **GIVEN** an account marked `RATE_LIMITED` by a 429 whose Retry-After hint persisted `reset_at` 20 minutes in the future and `blocked_at` set
- **WHEN** a periodic usage refresh fetches fresh usage showing available quota before that deadline
- **AND** no qualifying Free monthly reset transition matches the persisted deadline
- **THEN** the persisted row keeps status `RATE_LIMITED` with its `reset_at` and `blocked_at` intact
- **AND** once the deadline elapses, a later refresh may recover the account to `ACTIVE` through the compare-and-set path

#### Scenario: Confirmed blocked Free monthly reset permits peer recovery

- **GIVEN** replica A marked a Free account `RATE_LIMITED` with `blocked_at` and a persisted deadline matching that account's monthly window
- **AND** the 30-second minimum floor has elapsed
- **WHEN** replica B observes a real post-block transition from the matching monthly baseline into a new available monthly window
- **AND** the latest monthly sample remains below `100%`
- **THEN** replica B may compare-and-set the account to `ACTIVE` before the old persisted deadline
- **AND** a successful transition clears `reset_at` and `blocked_at`

#### Scenario: Generic 429 without matching reset evidence remains protected

- **GIVEN** an account has a future persisted cooldown from an upstream 429 or Retry-After hint
- **AND** fresh usage reports availability but no temporal monthly reset whose baseline matches that deadline
- **WHEN** any replica evaluates recovery
- **THEN** the account remains `RATE_LIMITED` until an ordinary recovery condition is met

#### Scenario: Peer replica does not flip a cooling account back

- **GIVEN** balancer instance A marked account X `RATE_LIMITED` from a 429 with no reset metadata
- **AND** account X's recorded usage is below 100%
- **WHEN** a second balancer instance sharing the same database runs account selection
- **THEN** account X is not selected
- **AND** the persisted row remains `RATE_LIMITED` with its `reset_at` deadline intact until the deadline elapses or strict reset-confirmed recovery succeeds

#### Scenario: Stale runtime cooldown does not unlock early recovery of a newer block

- **GIVEN** a replica holds expired runtime cooldown state left over from an earlier 429 of account X
- **AND** account X was since re-marked `RATE_LIMITED` by a peer replica with a newer `blocked_at` and a future persisted `reset_at`
- **WHEN** the replica evaluates account X with usage recorded after the newer `blocked_at`
- **AND** no strict reset-confirmed transition matches the newer block
- **THEN** account X stays `RATE_LIMITED` and is not selected until the persisted deadline elapses

#### Scenario: Concurrent newer block wins the recovery race

- **GIVEN** reset evidence qualifies a blocked Free account for early recovery
- **AND** another replica changes its status or either block marker before recovery commits
- **WHEN** the recovery compare-and-set evaluates the older snapshot
- **THEN** it does not overwrite the newer account row
- **AND** the account is not made routable from the stale evidence

#### Scenario: Exhausted Plus primary window remains protected

- **GIVEN** a Plus account is `RATE_LIMITED` with primary usage at `100%`
- **WHEN** a replica observes available long-window usage or a long-window reset
- **THEN** the Free monthly reset exception does not apply
- **AND** the account remains unavailable until its ordinary recovery condition is met

#### Scenario: Legacy row without reset_at is floored

- **GIVEN** a persisted `RATE_LIMITED` row with `blocked_at` five seconds ago and `reset_at` NULL
- **WHEN** a fresh balancer instance evaluates it during selection
- **THEN** the account stays `RATE_LIMITED` and is not selected
- **AND** once the 30-second floor has elapsed, recovery back to `ACTIVE` is permitted through the compare-and-set path

### Requirement: Transient balancer health signals are replica-local

Transient error counts, error-backoff windows, drain/probe health tiers, probe success streaks, and in-flight/lease pressure SHALL be maintained per replica
as advisory routing state and SHALL NOT require cross-replica agreement;
persisted account status, `reset_at`, and `blocked_at` transitions are the
only cross-replica health signals. Each replica SHALL converge on its own
observations.

#### Scenario: Peer may route to an account draining elsewhere

- **GIVEN** replica A has drained account X after locally observed transient errors
- **WHEN** replica B, which has recorded no errors for X, performs selection
- **THEN** replica B may select account X
- **AND** replica B backs off independently once its own error threshold for X is reached

### Requirement: Round-robin tie-breaking is decorrelated across replicas

The `round_robin` routing strategy SHALL order candidate accounts primarily by
planner cost and then by least-recently-selected time, and SHALL break any
remaining exact tie using a per-replica salt mixed into the account identifier
through a keyed hash. The salt SHALL be stable for the lifetime of a replica
process (not randomized per selection), SHALL default to the replica's HTTP
responses-session bridge instance identity, and SHALL fall back to the host
identity when no bridge instance identity is configured. Mixing the salt SHALL
change only the final tie-break: the planner-cost and least-recently-selected
ordering SHALL remain identical to selection without a salt, so only genuinely
tied candidates are reordered.

#### Scenario: Replicas with distinct salts spread an exact tie

- **GIVEN** two or more healthy eligible accounts that are exactly tied on
  planner cost and least-recently-selected time
- **AND** two replicas configured with distinct per-replica salts
- **WHEN** each replica selects with the `round_robin` strategy
- **THEN** the replicas MAY break the tie toward different accounts so load
  spreads across the equally-good candidates instead of herding onto one

#### Scenario: Primary ordering is unaffected by the salt

- **GIVEN** candidate accounts that differ in planner cost or
  least-recently-selected time
- **WHEN** account selection uses the `round_robin` strategy under any salt
- **THEN** the account with the lower planner cost, or when costs are equal the
  least-recently-selected account, is selected regardless of the salt value

#### Scenario: Single-replica selection is deterministic

- **GIVEN** a fixed set of candidate accounts and a fixed per-replica salt
- **WHEN** the `round_robin` strategy selects repeatedly with unchanged state
- **THEN** the same account is selected every time

### Requirement: Probing accounts receive bounded recovery admission

For routing strategies that use the health-tier candidate pool, the load balancer MUST give replica-local `PROBING` accounts bounded opportunities to receive recovery traffic while healthy accounts remain. A probing account MUST become due when it has never been selected or at least the fixed probe quiet interval has elapsed since its last selection. When one or more probing accounts are due, selection MUST admit only the oldest-due probing account, using account id as a stable tie-break, ahead of the healthy pool for that selection. An unbound sticky selection or a sticky selection that can fall back from its existing owner MUST reserve that admission in replica-local runtime state before releasing the runtime lock for sticky repository work, so concurrent requests cannot consume the same due interval. The reservation MUST retain both its timestamp token and the runtime version captured at reservation. It MUST remain provisional through selection-time sticky repository work, the final local lease check, and selection-state persistence, and MUST be committed only if both captured values remain current when selection returns the probing account. If either value becomes stale, selection MUST release the reservation and retry without returning the stale probe. Sticky delete or upsert decisions made during selection MUST remain provisional through hard-sticky cap classification, final lease admission, selection-state persistence, and the probe reservation commit when applicable. Every other outcome MUST release the reservation without consuming the interval. Provisional reserve/release operations MUST NOT advance the runtime health-observation version used to reject stale Force Probe settlement; a committed recovery admission MUST advance it. When no probing account is due, healthy-first ordering MUST remain unchanged.

Recovery admission MUST occur only after all ordinary account eligibility, cooldown, model, security, quota, and local account-cap gates, but before budget and `burn_first`/`normal`/`preserve` preference shortcuts that could otherwise mask the due account. A hard-sticky owner MAY remain in the candidate set despite its local account cap solely to preserve fail-closed ownership, but every wider fallback candidate MUST pass the cap gate. When every otherwise available fallback is cap-filtered, an unavailable but under-cap fallback MUST NOT suppress the stable local account-cap error or be selected through transient-backoff fallback. Non-cap availability before and after cap filtering MUST be evaluated over each complete fallback pool so cross-account opportunistic eligibility is preserved, and an established local cap error MUST NOT be replaced by the opportunistic burn-window error. Returning a local cap error MUST preserve the existing hard-sticky owner mapping without deleting or rebinding it. Recovery admission MUST NOT displace a selectable existing sticky owner merely to probe another account, and it MUST NOT change the behavior of routing strategies that intentionally bypass health-tier pool ordering.

#### Scenario: Due probing account progresses while a healthy account exists

- **GIVEN** one eligible healthy account and one eligible probing account whose last selection is older than the fixed probe quiet interval
- **WHEN** an unbound health-tier-aware selection occurs
- **THEN** the probing account is selected for one recovery attempt
- **AND** its selection timestamp prevents another bounded recovery admission until the quiet interval elapses again

#### Scenario: Recent probing account does not displace healthy routing

- **GIVEN** one eligible healthy account and one eligible probing account selected less than the fixed probe quiet interval ago
- **WHEN** health-tier-aware selection occurs
- **THEN** the healthy account is selected

#### Scenario: Routing policy cannot starve a due probe

- **GIVEN** an eligible probing account is due while a healthy `burn_first` or non-preserved account remains
- **WHEN** health-tier-aware selection applies routing-policy preferences
- **THEN** the due probing account receives the bounded recovery admission before those preferences

#### Scenario: Oldest due probing account rotates fairly

- **GIVEN** multiple eligible probing accounts are due while healthy accounts remain
- **WHEN** health-tier-aware selection occurs
- **THEN** only the probing account with the oldest selection timestamp is admitted
- **AND** account id deterministically breaks an exact timestamp tie

#### Scenario: Existing sticky owner is retained

- **GIVEN** a request has a selectable sticky owner on a healthy account
- **AND** another account is due for probing recovery
- **WHEN** sticky selection occurs
- **THEN** the existing owner remains selected
- **AND** the sticky mapping is not rebound for recovery sampling

#### Scenario: Concurrent unbound stickies share one recovery admission

- **GIVEN** a probing account is due while a healthy account remains
- **AND** multiple requests concurrently observe distinct sticky keys with no existing owner
- **WHEN** those requests perform sticky selection
- **THEN** at most one request selects the due probing account for that quiet interval
- **AND** the other requests observe the reservation and retain healthy-first routing

#### Scenario: Concurrent sticky fallbacks share one recovery admission

- **GIVEN** a probing account is due while a healthy account remains
- **AND** multiple sticky requests have existing owners that are temporarily unavailable
- **WHEN** those requests concurrently select from the wider fallback pool
- **THEN** at most one request selects the due probing account for that quiet interval
- **AND** the other fallback requests observe the reservation and retain healthy-first routing

#### Scenario: Lease race releases a provisional probe admission

- **GIVEN** a sticky request reserves a due probing account while it performs sticky persistence
- **AND** another request fills that account's local concurrency cap before the final lease check
- **WHEN** the reserving request rejects the probing account and selects or reports another outcome
- **THEN** the probing account's prior selection timestamp is restored
- **AND** the quiet interval is not consumed by traffic that was never admitted

#### Scenario: Released reservation does not invalidate Force Probe settlement

- **GIVEN** an accepted Force Probe is loading usage for a probing account
- **AND** a concurrent sticky request reserves and then releases that probing account without selecting it
- **WHEN** the Force Probe settles against otherwise unchanged runtime health
- **THEN** its success is not rejected as stale because of the provisional reservation
- **AND** reserve/release does not advance the runtime health-observation version

#### Scenario: Newer health observation invalidates a sticky probe reservation

- **GIVEN** a sticky request reserves a due probing account and begins sticky repository work
- **AND** a newer runtime health observation advances that account's version and changes its health tier before admission commits
- **WHEN** the stale sticky selection attempts to return the reserved probing account
- **THEN** selection releases the reservation and retries against the newer runtime state
- **AND** it does not return the stale probe or persist sticky affinity to it

#### Scenario: Saturated probing account is excluded from sticky fallback

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** a due probing fallback account is at its local concurrency cap
- **AND** an eligible healthy fallback remains below its cap
- **WHEN** sticky fallback selection occurs
- **THEN** the saturated probing account is excluded from the fallback pool
- **AND** the healthy account is selected without rebinding the sticky mapping to the saturated account

#### Scenario: Saturated-only sticky fallback reports local cap pressure

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** every wider fallback account is at its local concurrency cap
- **WHEN** sticky fallback selection cannot retain the owner
- **THEN** selection returns the stable local account-cap error
- **AND** it does not report global upstream unavailability or delete or rebind the sticky mapping

#### Scenario: Unavailable under-cap fallback does not mask cap pressure

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** every otherwise available wider fallback is at its local concurrency cap
- **AND** another wider fallback is under cap but unavailable because of quota, rate limit, cooldown, or account status
- **WHEN** sticky fallback selection cannot retain the owner
- **THEN** selection returns the stable local account-cap error
- **AND** the unavailable under-cap fallback does not cause global upstream-unavailability classification

#### Scenario: Opportunistic fallback cap pressure is classified over the complete pool

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** multiple wider fallbacks are opportunistically eligible when evaluated together
- **AND** every such fallback is at its local concurrency cap
- **WHEN** opportunistic sticky fallback selection cannot retain the owner
- **THEN** selection returns the stable local account-cap error
- **AND** it does not replace that reason with the opportunistic burn-window error

#### Scenario: Backoff-only fallback does not bypass local cap pressure

- **GIVEN** a hard-sticky owner is temporarily unavailable
- **AND** every normally usable wider fallback is at its local concurrency cap
- **AND** another under-cap fallback is still in transient error backoff
- **WHEN** sticky fallback selection evaluates the post-cap pool
- **THEN** it returns the stable local account-cap error
- **AND** it does not select or bind the backoff-only fallback

#### Scenario: Cap classification discards a provisional sticky deletion

- **GIVEN** an unavailable hard-sticky owner would otherwise be reallocated because of budget pressure
- **AND** every normally usable wider fallback is at its local concurrency cap
- **WHEN** selection finalizes the stable local account-cap error
- **THEN** any provisional delete or rebind decision is discarded
- **AND** the existing hard-sticky owner mapping remains unchanged

### Requirement: Trusted cyber intent narrows the existing account pool

Account routing MUST constrain an authenticated direct Responses WebSocket
turn requiring `trusted_cyber` by passing
`require_security_work_authorized=True` to the canonical selector before the
first upstream attempt and every later retry. The selector MUST apply the
constraint only to accounts already permitted by API-key, account, model,
service-tier, ownership, health, quota, affinity, concurrency, and failover
rules. Routing MUST NOT add an account, change the configured strategy, rebind
an owner, or fall back to an ordinary account.

#### Scenario: First attempt uses the capable pool
- **WHEN** an authenticated direct WebSocket turn establishes `trusted_cyber`
- **THEN** its first account-selection call requires a
  security-work-authorized account
- **AND** no ordinary account receives an upstream attempt

#### Scenario: Empty capable pool fails closed
- **WHEN** a required turn has no eligible security-work-authorized account
- **THEN** selection returns the existing typed
  `no_security_work_authorized_accounts` error
- **AND** its advisory states that no ordinary-account fallback occurred
- **AND** an earlier reactive or account/model error cannot replace that typed
  capability-routing result
- **AND** ordinary routing is not attempted

#### Scenario: Ordinary routing is unchanged
- **WHEN** an authenticated direct WebSocket turn has neither a trusted signal
  nor required lineage
- **THEN** selection receives the same scope, strategy, ownership, admission,
  and retry inputs as before this change

### Requirement: Invalid refresh tokens require account re-authentication

The system MUST classify an upstream OAuth `invalid_refresh_token` refresh
failure as permanent, persist the affected account as re-authentication required
through the guarded refresh-account status path, and exclude that account from
normal account selection until an operator reauthenticates or imports fresh
credentials.

#### Scenario: OAuth invalid-refresh-token response removes the account from routing

- **GIVEN** an active account attempts a token refresh
- **WHEN** upstream OAuth returns `invalid_refresh_token`
- **THEN** the refresh path persists the account status as `reauth_required`
- **AND** the account is not selected for subsequent routed requests until fresh
  credentials are supplied

### Requirement: Re-authentication-required accounts remain request-routable

The system MUST distinguish request routability from refresh-token eligibility. `active` accounts MUST be request-routable. A `reauth_required` account MUST remain request-routable only while its stored access token is not known to be expired; paused and deactivated accounts MUST remain excluded.

This status baseline is canonical for proxy selection, owner-bound affinity, warmup, automations, API-key account pools and scopes, probes, access-token-authenticated usage and reset-credit operations, and dashboard projections of routable capacity. Capability-specific references to active, eligible, or hard-unavailable accounts MUST apply this baseline unless a stricter credential-expiry, security, ownership, model, quota, cooldown, or operator-policy gate is explicitly required.

Selecting a routable `reauth_required` account MUST use its stored access token without proactive refresh-token exchange. Its sticky, bridge, file, response, and realtime ownership MUST remain bound while that token is unexpired. Once a known access-token expiry is reached, new proxy selection and live bridge reuse MUST stop before upstream I/O. Movable soft affinity MAY fail over, while hard account-owned continuity MUST remain fail-closed rather than crossing accounts.

A permanent forced-refresh failure while serving a movable request MUST release the account's lease and exclude it from that request's remaining attempts. The failure MUST NOT create a process-wide routing block before the stored access token's known expiry.

#### Scenario: Token-invalidated account remains in the pool

- **GIVEN** account A is `reauth_required` with a usable stored access token
- **WHEN** an ordinary proxy or supporting access-token operation selects an account
- **THEN** account A remains eligible after all other applicable gates
- **AND** its refresh token is not proactively exchanged

#### Scenario: Warning state preserves ownership

- **GIVEN** account A owns sticky or hard continuity
- **WHEN** account A becomes `reauth_required` with an unexpired stored access token
- **THEN** the ownership remains bound to account A
- **AND** the transition alone does not delete or rebind continuity

#### Scenario: Expired warning account is quiesced locally

- **GIVEN** account A is `reauth_required`
- **AND** its stored access token has reached its known expiry
- **WHEN** a new proxy request selects an account or considers bridge reuse
- **THEN** account A is rejected before upstream I/O
- **AND** hard account-owned continuity does not move to another account

#### Scenario: All expired warning accounts report reauthentication

- **GIVEN** every otherwise scoped account is `reauth_required` with a known-expired access token
- **AND** an additional-quota evidence gate would otherwise reject those accounts first
- **WHEN** account selection runs
- **THEN** selection fails with an explicit message that all accounts require reauthentication

#### Scenario: Current request excludes a rejected warning account

- **GIVEN** a movable request selected account A
- **AND** forced refresh fails permanently after upstream rejects A's access token
- **WHEN** the request retries selection
- **THEN** account A is excluded from that request's remaining attempts
- **AND** account A may still be considered by a later independent request

#### Scenario: Request-routable account can be selected for scoped routing

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is offered as selectable

#### Scenario: Hard-blocked account cannot be newly selected for scoped routing

- **GIVEN** account A is paused or deactivated
- **WHEN** any routing strategy or account-scoped picker evaluates account A
- **THEN** account A is not selectable

#### Scenario: Re-authentication-required account cannot be paused into resumable state

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator attempts to pause account A
- **THEN** the request is rejected
- **AND** account A remains `reauth_required`

### Requirement: Overload rejections deprioritize the account for fresh selection

When upstream rejects a fresh admission for an account as overloaded
(`server_is_overloaded` or `overloaded_error`), the proxy MUST record the
rejection, at the point where account health is written for it, in a
replica-local per-account window that is independent of the transient error
count and MUST NOT be reset by later successes on that account. When at least three rejections land inside a 120-second window, the
proxy MUST deprioritize the account for fresh (unbound) selection for a
bounded interval that grows exponentially with consecutive trips (60 seconds
base, capped at 600 seconds, decaying to the base after 30 minutes without a
trip); the level MUST saturate once the cap is reached so sustained overload
cannot grow it without bound, and a trip while already deprioritized MUST NOT
shorten the deadline.
Deprioritization MUST be soft: selection first runs over the candidates not in
overload backoff and, when the configured strategy and budget gates select
none of them, runs again over the full candidate pool exactly as before. It
MUST apply wherever a NEW account is chosen for a request — unbound selection
and the sticky path's fresh binding, reallocation, or fallback pick — and MUST
NOT apply to an established sticky owner, a continuity owner, or a
hard-affinity owner. The
proxy MUST log when the backoff engages. The failure classification, the
failover decision, the existing transient error penalty, and the status and
body returned to the client MUST remain unchanged.

#### Scenario: Warm sessions keep masking the generic error counters

- **GIVEN** account A is rejected as overloaded on fresh admissions while its
  bridge-reuse and sticky sessions keep succeeding
- **WHEN** three rejections arrive within 120 seconds
- **THEN** account A enters overload backoff even though its transient error
  count is zero
- **AND** fresh selection skips account A while another candidate is available

#### Scenario: Backoff never empties the pool

- **GIVEN** every selectable account is in overload backoff
- **WHEN** a fresh request selects an account
- **THEN** selection proceeds over the full candidate pool as if no account
  were backed off

#### Scenario: Backoff yields to an ineligible remainder

- **GIVEN** account A is in overload backoff and the configured strategy
  selects none of the other accounts (rate-limited, cooling down, in generic
  error backoff, or excluded by the strategy's budget gates)
- **WHEN** a fresh request selects an account
- **THEN** account A is selected rather than failing the request or reporting
  an account-cap error

#### Scenario: A previously unseen sticky key binds away from the backed-off account

- **GIVEN** account A is in overload backoff and account B is selectable
- **WHEN** a request carrying a session or prompt-cache key with no established
  owner selects an account
- **THEN** the new binding is made to account B
- **AND** a request whose key already maps to account A keeps using account A

#### Scenario: HTTP-status overload rejections keep their code

- **GIVEN** upstream answers a fresh admission with an HTTP 5xx whose body
  carries `server_is_overloaded`
- **WHEN** the same-account transient retries are exhausted and health is
  written after settlement
- **THEN** the health write carries `server_is_overloaded` rather than a
  collapsed `server_error`, so the rejection counts toward the account's
  overload window

#### Scenario: Recovery-probe reservation follows the selected pool

- **GIVEN** a fresh request whose selection ran over the overload-free
  candidates
- **WHEN** the selected account is a due recovery probe that needs a
  reservation
- **THEN** the reservation is taken from that same overload-free pool, so an
  older due probe skipped by the overload pass cannot invalidate the selection

#### Scenario: Pinned sessions are not denied by overload backoff

- **GIVEN** account A is in overload backoff
- **WHEN** a request hard-pinned to account A (continuity owner, sticky
  session, or file affinity) selects an account
- **THEN** the pin is honored exactly as before

#### Scenario: Consecutive trips back off longer, bounded

- **GIVEN** account A trips the window repeatedly with each burst starting when
  the previous backoff expires
- **WHEN** the backoff deadline is computed for each trip
- **THEN** the interval doubles from 60 seconds and never exceeds 600 seconds
- **AND** after 30 minutes without a trip the next trip returns to 60 seconds

#### Scenario: Non-overload transient errors do not feed the window

- **GIVEN** account A returns a transient `server_error`
- **WHEN** the proxy records account health
- **THEN** the generic transient error is recorded as before
- **AND** the overload window for account A is unchanged

### Requirement: Upstream overload rejections back off and then isolate the account

The balancer SHALL keep a replica-local sliding window of upstream overload admission rejections (`server_is_overloaded`, `overloaded_error`) per account that successes do not reset. When the window trips, the account SHALL enter a bounded, exponentially growing **soft backoff** during which fresh unbound selection and fresh sticky bindings prefer other candidates. When the backoff level reaches the isolation trip level, the account SHALL instead be **isolated** for the dashboard setting `proxy_overload_isolation_seconds` (default 1800; `0` disables isolation and keeps the soft backoff only; the environment variable `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS` is the deprecated fallback the dashboard inherits while its value is unset). The window used for an isolation MUST be the one carried by the balancer's most recent request snapshot, never a settings read from the error funnel. While isolated, established soft sticky owners MAY be released as specified by `sticky-session-operations`. In both stages the account MUST be dropped from a candidate pool only while at least one other candidate remains, and the configured strategy MUST judge eligibility of the remaining pool: when it rejects every overload-free candidate, selection MUST fall back to the full pool exactly as before. The backoff level MUST NOT decay while the account is backed off or isolated; it decays only after a quiet interval measured from the later of the last trip and the backoff deadline. Hard continuity owners MUST NOT be moved by either stage. The balancer MUST emit a warning when isolation engages, naming the account, level and isolation interval.

#### Scenario: Sustained rejection escalates from soft backoff to isolation

- **GIVEN** an account whose overload window has tripped twice (soft backoff)
- **WHEN** it trips a third time
- **THEN** the account is isolated for the configured isolation interval instead of the next soft interval
- **AND** a warning `Account overload isolation engaged` is logged with the level and interval

#### Scenario: Isolation is disabled by a zero interval

- **GIVEN** the dashboard stores `proxy_overload_isolation_seconds = 0` (or the column is NULL and `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS=0`)
- **WHEN** an account's overload window trips at or beyond the isolation level
- **THEN** it receives the capped soft backoff interval and is never marked isolated

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment leaves the isolation window at 1800 seconds and `PUT /api/settings` stores `proxyOverloadIsolationSeconds: 240`
- **WHEN** a request has been served after the change and an account's overload window then trips at the isolation level
- **THEN** the account is isolated for 240 seconds without a restart

#### Scenario: Leaving isolation while still rejected re-isolates

- **GIVEN** an account whose isolation deadline just passed
- **WHEN** its overload window trips again within the decay interval
- **THEN** its level has not decayed and the account is isolated again

#### Scenario: A lone candidate is never held out

- **GIVEN** the only selectable account is isolated
- **WHEN** a request selects an account
- **THEN** the isolated account is selected rather than failing with `No available accounts`

### Requirement: Weighted strategies discount recent upstream error rate

The balancer SHALL keep a replica-local window (600 s) of upstream outcomes per account: successes recorded by `record_success` and the account-attributable transient failures recorded by `record_errors`. Rate-limit, quota, permanent and account-neutral failures MUST NOT be counted. When the dashboard setting `proxy_account_error_rate_weighting_enabled` is true (default; the environment variable `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED` is the deprecated fallback the dashboard inherits while its value is unset) and the window holds at least 10 outcomes, the `capacity_weighted` and `relative_availability` strategies MUST multiply the candidate's draw weight by `max(0.05, 1 - error_rate)`; with fewer outcomes or the setting disabled the multiplier MUST be neutral. The switch MUST be read from the request's `RoutingTunables` snapshot when states are built, never from the process settings inside selection. The multiplier MUST NOT change `relative_availability` top-k membership or any deterministic probe pick, and deterministic strategies (`round_robin`, `usage_weighted`, `fill_first`, `sequential_drain`, `reset_drain`, `single_account`) MUST be unaffected. The discount MUST lift as the window clears without requiring a success.

#### Scenario: A flaky account receives proportionally less weighted traffic

- **GIVEN** two accounts with equal remaining credits under `capacity_weighted`
- **AND** one of them recorded 12 transient failures interleaved with 12 successes in the last ten minutes (so its `error_count` latch is zero)
- **WHEN** fresh selections are drawn
- **THEN** the flaky account is drawn about half as often as the clean one
- **AND** it is still drawn (the weight floor keeps sampling it)

#### Scenario: Thin evidence is neutral

- **GIVEN** an account with nine failures and no successes in the window
- **WHEN** its draw weight is computed
- **THEN** the multiplier is `1.0`

#### Scenario: Weighting can be disabled

- **GIVEN** the dashboard stores `proxy_account_error_rate_weighting_enabled = false` (or the column is NULL and `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED=false`)
- **WHEN** an account has failed every request in the window
- **THEN** its draw weight multiplier is `1.0`

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment sets `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED=true` and the dashboard stores `false`
- **WHEN** states are built for a weighted selection
- **THEN** every candidate's multiplier is `1.0`

### Requirement: Resilience toggles follow the dashboard value

Soft drain (the draining/probing health tiers), the deterministic failover decision and the circuit-breaker selection gate MUST be controlled by the `dashboard_settings` columns `soft_drain_enabled`, `deterministic_failover_enabled` and `circuit_breaker_enabled`. A NULL column MUST inherit the process environment value (the deprecated `CODEX_LB_*` alias) and then the code default, and a non-NULL column MUST win over both; the effective value MUST come from the single `configuration-tiers` resolver, and the settings API MUST report each toggle's effective value and provenance. Account selection MUST resolve the three toggles once from the dashboard-settings snapshot its caller obtained before entering runtime locks — the same snapshot that produced the concurrency caps — MUST apply that resolution to every reload of its selection inputs (sticky and non-sticky retries, exclusion- and security-filtered pools) and to opportunistic admission, and MUST NOT read the database, await the settings cache or read `get_settings().<toggle>` for them while holding a runtime lock or inside the retry loop. Force Probe settlement MUST take one snapshot before acquiring the account lock. The background state builds — the quota planner tick, the quota planner forecast endpoint and the usage-refresh recovery reconciliation — MUST resolve soft drain from one dashboard-settings snapshot taken per tick or per request outside any runtime lock (the settings cache, or the dashboard-settings row the usage-refresh cycle already read) and pass it into the state build, so the health tier they compute follows the dashboard toggle; they MUST NOT resolve the environment layer while a snapshot is available. Only a caller with no snapshot at all (tests, tools) resolves the environment layer, which is the pre-dashboard behaviour. Changing a toggle in the dashboard MUST take effect on the next selection on every replica without a restart.

#### Scenario: Dashboard turns soft drain off

- **GIVEN** `CODEX_LB_SOFT_DRAIN_ENABLED` is unset (default on) and an operator sets soft drain off in the dashboard
- **WHEN** the next selection evaluates an account whose primary usage is above the fixed drain threshold
- **THEN** the account stays in the healthy tier instead of entering the draining tier
- **AND** no database read or settings-cache await happened under the runtime lock

#### Scenario: Background state builds follow the dashboard soft-drain value

- **GIVEN** `CODEX_LB_SOFT_DRAIN_ENABLED` is unset (default on) and the dashboard stores `soft_drain_enabled = false`
- **WHEN** the quota planner tick or forecast endpoint builds its account states, or the usage-refresh recovery evaluates a recoverable account
- **THEN** the states are built with soft drain off, so an account above the fixed drain threshold stays in the healthy tier
- **AND** the snapshot was taken once for that tick or request, outside any runtime lock

#### Scenario: Dashboard turns deterministic failover off

- **GIVEN** an operator has set deterministic failover off in the dashboard
- **WHEN** a stream, compact or WebSocket attempt fails before the first event with a failover-eligible classification
- **THEN** the proxy surfaces the failure instead of retrying on the next account

#### Scenario: Dashboard enables the circuit-breaker gate the environment left off

- **GIVEN** `CODEX_LB_CIRCUIT_BREAKER_ENABLED=false` and an operator turns the circuit breaker on in the dashboard
- **AND** every account's breaker is open
- **WHEN** a selection runs
- **THEN** the balancer reports the upstream as degraded because the breakers are open, without a restart

#### Scenario: Inherited toggle follows a later environment change

- **GIVEN** a toggle's dashboard column is NULL
- **WHEN** the process environment value changes and the process restarts
- **THEN** the new environment value applies and the settings API reports `source: "env"` (or `"default"` when it equals the code default)

### Requirement: Routing weights and overload isolation are dashboard settings

The in-flight pressure penalty (`proxy_account_inflight_penalty_pct`), the leased-token weight (`proxy_account_lease_token_weight`), the account lease TTL (`proxy_account_lease_ttl_seconds`), the overload isolation window (`proxy_overload_isolation_seconds`) and the error-rate weighting switch (`proxy_account_error_rate_weighting_enabled`) MUST be `dashboard_settings` columns of the same name, resolved as code default < environment < dashboard: a NULL column inherits the process environment value (or the code default), and a non-NULL column wins over the environment. The first-boot seed and the migration MUST leave the columns NULL. The settings API MUST expose each effective value with a `provenance` entry and accept the tri-state update (omitted = unchanged, `null` = inherit, value = store) with the bounds of the corresponding `Settings` field; the in-flight penalty MUST additionally be bounded at 100 on write (an inherited environment value above 100 MUST still be readable), and a dashboard lease TTL MUST satisfy the same `account-lease-ttl-covers-*` timeout invariants that startup validation applies to the environment value, evaluated against the effective request budgets. The load balancer MUST NOT read the process settings for these values on the request path: the proxy service MUST resolve them once per selection or lease operation from the cached dashboard snapshot it already holds for that operation (the snapshot the concurrency caps are derived from) and pass them into account selection, opportunistic admission and lease acquisition, and the balancer MUST thread that snapshot through every runtime-lock section of the operation without reading settings. A path that carries no request snapshot (the stream error funnel recording an overload rejection, an unkeyed bridge session reacquiring its lease) MUST reuse the balancer's most recent request snapshot rather than read settings; the environment applies only before the first request has been served. The background state builds (the quota planner tick and forecast endpoint, the usage-refresh recovery reconciliation) MUST resolve the knobs from the dashboard-settings snapshot they take once per tick or per request and pass them into the state build; only a caller with no snapshot at all (tests, tools) resolves the environment alone. A changed value takes effect within the settings cache TTL without a restart, with these runtime semantics: a new isolation window applies to trips recorded after the change only — an account already isolated keeps its existing deadline, and storing `0` does not lift an active isolation; a new lease TTL applies at the next stale-lease reclaim pass to every existing lease (judged by its acquisition time). The environment variables remain as deprecated fallbacks for one release and are removed in the next minor.

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment sets `CODEX_LB_PROXY_ACCOUNT_INFLIGHT_PENALTY_PCT=2.5` and the dashboard stores `proxy_account_inflight_penalty_pct = 10`
- **WHEN** account states are built for a selection
- **THEN** each in-flight request adds 10 percentage points of pressure, not 2.5

#### Scenario: Background state builds resolve the knobs from the dashboard snapshot

- **GIVEN** the process environment leaves `proxy_account_inflight_penalty_pct` at 2.5 and the dashboard stores `proxy_account_inflight_penalty_pct = 37.5`
- **WHEN** the quota planner tick or forecast endpoint builds its account states, or the usage-refresh recovery evaluates a recoverable account
- **THEN** the state build receives the routing tunables resolved from the dashboard snapshot (in-flight penalty 37.5), not the environment value
- **AND** no settings read happens under a runtime lock

#### Scenario: Cleared dashboard value returns to the environment

- **GIVEN** the dashboard stores `proxy_account_lease_ttl_seconds = 1200` while the environment sets `CODEX_LB_PROXY_ACCOUNT_LEASE_TTL_SECONDS=1800`
- **WHEN** `PUT /api/settings` sends `proxyAccountLeaseTtlSeconds: null`
- **THEN** the response reports the effective TTL 1800 with `provenance.proxy_account_lease_ttl_seconds.source` `"env"`
- **AND** the next request snapshot judges stale leases against 1800 seconds

#### Scenario: Isolation window change applies to future trips only

- **GIVEN** an account isolated for 1800 seconds with 1000 seconds remaining
- **WHEN** the dashboard stores `proxy_overload_isolation_seconds = 0` (or 240)
- **THEN** the account stays isolated until its existing deadline
- **AND** the next account whose window trips at the isolation level receives the soft backoff only (or 240 seconds)

#### Scenario: Lease TTL change applies at the next reclaim pass

- **GIVEN** a response-create lease acquired 700 seconds ago while the effective lease TTL was 900
- **WHEN** the dashboard stores `proxy_account_lease_ttl_seconds = 600` and the next selection or lease acquisition runs its stale-lease reclaim
- **THEN** that lease is reclaimed as stale in that pass

#### Scenario: Selection never reads settings under the runtime lock

- **GIVEN** a request whose selection acquires the balancer's runtime lock
- **WHEN** the balancer builds states, reclaims stale leases and evaluates draw weights
- **THEN** every knob comes from the `RoutingTunables` snapshot passed in for that operation and no settings or database read happens inside the lock section

#### Scenario: Out-of-bounds value is rejected

- **WHEN** `PUT /api/settings` sends `proxyAccountInflightPenaltyPct: 150`, `proxyAccountLeaseTtlSeconds: 0`, or a lease TTL below the proxy or compact request budget (for example 120 with the default 600 s budget)
- **THEN** the request is rejected with a validation error naming the violated invariant and the stored values are unchanged

### Requirement: Code-less upstream HTTP 429 rejections enter a short replica-local burst cooldown

When upstream answers a stream dispatch for a selected account with HTTP 429 whose error body carries no error code or type (normalized to `upstream_error`, classified `retryable_transient`), the proxy MUST, at the point where account health is written for that failure, record a replica-local per-account **burst cooldown** on the account's runtime state in addition to the existing transient error penalty. The cooldown deadline MUST be `now + clamp(retry_after, 5 s, 30 s)`, where `retry_after` is the upstream `Retry-After` value and a missing value applies 5 s; a rejection that arrives while a cooldown is already active MUST extend the deadline and MUST NOT shorten it. While the cooldown is active the account MUST be treated exactly as an account in overload soft backoff wherever a NEW account is chosen for a request — fresh (unbound) selection and the sticky path's fresh binding, reallocation, or fallback pick: it MUST be dropped from a candidate pool only while at least one other candidate remains, and when the configured strategy and budget gates select none of the remaining candidates, selection MUST run again over the full pool exactly as before. The cooldown MUST NOT engage the overload isolation stage, MUST NOT feed the overload rejection window, MUST NOT move an established sticky owner, a continuity owner, or a hard-affinity owner, MUST NOT write `RuntimeState.cooldown_until`, the persisted account status, `reset_at`, or `blocked_at`, MUST NOT change the failure classification or the status and body returned to the client, and MUST NOT be exposed as a new setting. A 429 that carries a rate-limit or quota code (`rate_limit_exceeded`, `usage_limit_reached`) MUST keep the existing rate-limit handling and MUST NOT engage the burst cooldown. The proxy MUST log a warning when the cooldown engages, naming the account under the configured redaction policy, the applied cooldown seconds, and the upstream `Retry-After` value.

#### Scenario: Cooldown steers unbound selection while a sibling exists

- **GIVEN** account A just returned a code-less HTTP 429 to a stream dispatch and account B is selectable
- **WHEN** a fresh unbound request selects an account within 5 seconds of the rejection
- **THEN** account B is selected
- **AND** a request arriving after the cooldown deadline may select account A again without any success having been recorded

#### Scenario: Single-candidate pool still serves

- **GIVEN** account A is the only selectable account and is in burst cooldown
- **WHEN** a fresh request selects an account
- **THEN** account A is selected rather than failing with `No available accounts` or an account-cap error

#### Scenario: Established sticky owner and hard continuity owners are kept

- **GIVEN** account A is in burst cooldown and account B is selectable
- **WHEN** a request whose `prompt_cache_key` already maps to account A, or a request hard-bound to account A by `previous_response_id`, a file pin, or turn-state ownership, selects an account
- **THEN** account A serves the request
- **AND** the mapping is not rebound to account B

#### Scenario: Persisted status is untouched

- **GIVEN** account A is `ACTIVE`
- **WHEN** a code-less HTTP 429 engages the burst cooldown for account A
- **THEN** the persisted status stays `ACTIVE` and `reset_at` and `blocked_at` are unchanged
- **AND** `RuntimeState.cooldown_until` and the overload rejection window for account A are unchanged
- **AND** a peer replica that has not observed the rejection may still select account A

#### Scenario: Retry-After sets the floor, clamped to 30 seconds

- **GIVEN** upstream answers with a code-less HTTP 429 carrying `Retry-After: 12`
- **WHEN** the burst cooldown is recorded
- **THEN** the cooldown lasts 12 seconds
- **AND** a `Retry-After: 120` on a later rejection yields a 30-second cooldown, and a `Retry-After: 1` yields a 5-second cooldown

#### Scenario: Coded 429 keeps the existing rate-limit handling

- **GIVEN** upstream answers a stream dispatch with HTTP 429 whose body carries `rate_limit_exceeded` or `usage_limit_reached`
- **WHEN** the proxy records account health
- **THEN** the account is marked rate-limited or cooling down as before, with its persisted status and `reset_at` written by the existing rate-limit path
- **AND** the burst cooldown is not engaged

#### Scenario: Transient penalty is still recorded

- **GIVEN** account A returns a code-less HTTP 429 and the request fails over or surfaces the failure
- **WHEN** the proxy records account health
- **THEN** the existing transient error penalty is recorded for account A exactly as before
- **AND** the burst cooldown engages alongside it
- **AND** a warning `Account burst backoff engaged` is logged with the applied seconds and the upstream `Retry-After` value

#### Scenario: Keyed stream engages the cooldown at rejection time

- **GIVEN** account A returns a code-less HTTP 429 to a stream on an API key whose usage reservation defers the health write until after settlement
- **WHEN** the rejection is observed
- **THEN** the burst cooldown for account A is engaged immediately, before the replacement dispatch or backoff wait
- **AND** the deferred transient penalty, written after settlement, does not extend the cooldown deadline

