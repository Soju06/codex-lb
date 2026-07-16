# account-routing Specification

## Purpose
TBD - created by archiving change add-relative-availability-routing. Update Purpose after archive.
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

#### Scenario: Upstream 429 marks only the selected account

- **GIVEN** account A is selected for a request
- **AND** upstream returns a rate-limit response for that request
- **WHEN** the proxy records the penalty
- **THEN** it marks account A as rate-limited or cooling down
- **AND** it does not create model-scoped or transport-scoped upstream cooldown buckets without upstream evidence

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

When the upstream rate-limit error carries no explicit reset metadata
(`resets_at`/`resets_in_seconds`), the resolved cooldown deadline SHALL be
persisted on the account row (`reset_at`) so the cooldown survives process
restarts and is visible to all replicas sharing the database: a parsed
Retry-After hint deadline SHALL be persisted rounded up to the next whole
second (persistence stores `reset_at` as an integer, so a short or fractional
hint MUST NOT truncate down to an already-elapsed deadline), and when the
cooldown comes from the error-count backoff fallback the persisted deadline
SHALL be at least `RATE_LIMITED_MIN_COOLDOWN_SECONDS` (30 seconds) in the
future. Explicit upstream reset metadata, when present, SHALL continue to be
persisted as-is.
The marking replica's in-process cooldown MAY remain shorter than the
persisted deadline so its existing fresh-usage recovery gate is unchanged.

#### Scenario: Compound minute-and-second hint sets the full cooldown

- **GIVEN** an upstream 429 whose message says "try again in 6m0s"
- **WHEN** the balancer records the rate limit for the account
- **THEN** the account cooldown lasts 360 seconds
- **AND** the account is not re-selected until that cooldown elapses

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

### Requirement: Re-authentication-required accounts are not selectable

When an account credential/session is invalidated but the upstream account is not known to be disabled, the system MUST mark the account `reauth_required`. The selector MUST remove `reauth_required` accounts from every routing strategy and hard-affinity fallback until the account is re-authenticated. Operator pickers that configure single-account routing or account-scoped routing MUST only offer accounts that are not hard-blocked by paused, reauth-required, or deactivated status.

#### Scenario: Token invalidated account leaves the pool

- **GIVEN** account A is `reauth_required`
- **AND** account B is active
- **WHEN** a proxy request selects an account
- **THEN** account B is selected
- **AND** account A is not considered an eligible candidate

#### Scenario: Hard-blocked account cannot be newly selected for scoped routing

- **GIVEN** account A is paused, reauth-required, or deactivated
- **WHEN** an operator opens a scoped account-routing picker
- **THEN** account A is not offered as a new selectable account

#### Scenario: Re-authentication-required account cannot be paused into resumable state

- **GIVEN** account A is `reauth_required`
- **WHEN** an operator attempts to pause account A
- **THEN** the request is rejected
- **AND** account A remains `reauth_required`

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

A replica that did not observe the upstream 429 MUST NOT transition a
`RATE_LIMITED` account to `ACTIVE` while the persisted `reset_at` deadline is
in the future, regardless of the account's recorded usage. For `RATE_LIMITED`
rows with `blocked_at` set but no persisted `reset_at` (legacy rows written
before cooldown persistence), replicas MUST hold the account `RATE_LIMITED`
until at least `blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS`. Recovery
transitions MUST be written through the compare-and-set status update
(`update_status_if_current`) so a stale snapshot cannot clobber a newer
marking.

This constraint applies to every recovery path that writes account status,
including the usage-refresh reconcile path: a usage refresh that observes
available quota for a `RATE_LIMITED` account with `blocked_at` set MUST NOT
rewrite the account to `ACTIVE` (or clear `reset_at`/`blocked_at`) while the
persisted cooldown deadline — `reset_at`, or the
`blocked_at + RATE_LIMITED_MIN_COOLDOWN_SECONDS` floor when `reset_at` is
NULL — is still in the future. Only the replica that observed the 429 MAY
recover the account earlier, through its runtime-cooldown-gated fresh-usage
path; a replica's runtime cooldown state counts as observing the current 429
only when its runtime block marker is at least as recent as the effective
persisted `blocked_at` — leftover runtime state from an earlier 429 MUST NOT
unlock early recovery of a newer block. `RATE_LIMITED` rows without
`blocked_at` (stale window-derived markings) keep the existing fresh-usage
recovery.

#### Scenario: Usage refresh does not clear a running Retry-After cooldown

- **GIVEN** an account marked `RATE_LIMITED` by a 429 whose Retry-After hint persisted `reset_at` 20 minutes in the future and `blocked_at` set
- **WHEN** a periodic usage refresh fetches fresh usage showing available quota before that deadline
- **THEN** the persisted row keeps status `RATE_LIMITED` with its `reset_at` and `blocked_at` intact
- **AND** once the deadline elapses, a later refresh may recover the account to `ACTIVE` through the compare-and-set path

#### Scenario: Peer replica does not flip a cooling account back

- **GIVEN** balancer instance A marked account X `RATE_LIMITED` from a 429 with no reset metadata
- **AND** account X's recorded usage is below 100%
- **WHEN** a second balancer instance sharing the same database runs account selection
- **THEN** account X is not selected
- **AND** the persisted row remains `RATE_LIMITED` with its `reset_at` deadline intact until the deadline elapses

#### Scenario: Stale runtime cooldown does not unlock early recovery of a newer block

- **GIVEN** a replica holds expired runtime cooldown state left over from an earlier 429 of account X
- **AND** account X was since re-marked `RATE_LIMITED` by a peer replica with a newer `blocked_at` and a future persisted `reset_at`
- **WHEN** the replica evaluates account X with usage recorded after the newer `blocked_at`
- **THEN** account X stays `RATE_LIMITED` and is not selected until the persisted deadline elapses

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

