# account-routing Delta

## ADDED Requirements

### Requirement: A single account's rejection is not the pool's rejection

When a pre-visible upstream failure classified `rate_limit`, `quota`, or `retryable_transient` occurs on a request that is not owner-bound, the proxy MUST exclude the rejecting account for the remainder of that request and reselect from the accounts that remain, and MUST repeat this until either a selected account serves the request or no non-excluded candidate remains. The proxy MUST NOT bound this walk by a fixed attempt count unrelated to the size of the usable pool.

The walk MUST terminate. Termination MUST be guaranteed by three independent bounds: the request budget deadline that already clamps every attempt; a runaway ceiling on the number of account attempts in one request, which MUST exceed the largest pool the deployment supports so that it can never become the ordinary bound; and a monotone-progress invariant requiring every `failover_next` outcome to grow the request-scoped excluded-account set. When a `failover_next` outcome does not grow that set, the proxy MUST log a warning naming the request and MUST terminate the walk rather than reselect.

The monotone-progress invariant governs failover outcomes only. An account-capacity recovery that deliberately re-admits a previously excluded account — waiting for a local cap to clear rather than rejecting the account — MUST be allowed to remove its own exclusion, MUST NOT be reported as a progress failure, and MUST remain bounded by the request budget. A walk that could re-admit an account on failover evidence would not terminate; a walk that could not re-admit on capacity evidence would lose a recovery path that exists today.

The proxy MUST record account health exactly once per attempted account per request. A walk across N accounts MUST produce N health writes, not N writes per attempt.

Owner-bound requests are outside this requirement: a request that cannot move to another account MUST continue to take the bounded same-account path and surface its original rejection unchanged.

When a walk ends without a served response, the proxy MUST record which bound ended it — a non-retryable failure, an exhausted pool, the request deadline, the runaway ceiling, or a progress failure. Those outcomes are operationally different and MUST be distinguishable after the fact; collapsing them into one undifferentiated "surface" leaves an operator unable to tell a bad request from an exhausted fleet.

#### Scenario: A usable sibling serves what one account rejected

- **GIVEN** accounts A, B and C are selectable and a request is not owner-bound
- **WHEN** account A answers the dispatch with an upstream HTTP 429 before any downstream-visible output
- **THEN** account A is excluded for this request and account B is selected
- **AND** when account B also rejects, account C is selected
- **AND** when account C serves the request the client receives that response, not account A's 429

#### Scenario: The walk is not capped at the legacy attempt constant

- **GIVEN** a pool of more than three selectable accounts
- **WHEN** the first three attempted accounts each return a pre-visible `rate_limit` failure
- **THEN** the proxy attempts a fourth account rather than surfacing the third account's failure

#### Scenario: Health is written once per attempted account

- **GIVEN** a walk that attempts five accounts before one serves the request
- **WHEN** the request completes
- **THEN** exactly five account-health writes were recorded, one per attempted account

#### Scenario: A selector that cannot make progress terminates the walk

- **GIVEN** account selection returns an account that is already in the request's excluded set
- **WHEN** the proxy evaluates the failover outcome
- **THEN** a warning is logged for the request
- **AND** the walk terminates through the pool-terminal path instead of reselecting

#### Scenario: Owner-bound requests keep today's behaviour

- **GIVEN** a request bound to account A by a required previous-response owner, a file pin, turn-state ownership, or the `single_account` routing strategy
- **WHEN** account A returns a pre-visible failure
- **THEN** the proxy does not walk the pool
- **AND** the request takes the bounded same-account retry path and then surfaces account A's failure unchanged

### Requirement: A walk proves exhaustion from its own attempts

When a walk has attempted every candidate the selector offered and excluded each of them on exhaustion evidence, the proxy MUST treat the pool as exhausted, whether or not the persisted-state exhaustion predicate can see it yet.

The proxy MUST NOT make this conclusion depend on a background or debounced usage refresh, which cannot land before the terminal decision of the request that provoked it. It MUST NOT depend on writing a usage sample onto the transient account state either: the runtime state the health write persists carries the account's status but not that sample, so a sample written during the attempt does not survive to be read back.

The walk therefore MUST carry its own per-account evidence to the terminal decision, and the terminal decision MUST accept it. The persisted-state probe remains authoritative for the case the walk cannot speak to — a request that never attempted the whole pool, because selection refused it earlier.

#### Scenario: Every attempted account was exhausted

- **GIVEN** a walk that attempted every account the selector offered
- **AND** each of them was excluded on usage-limit or quota evidence
- **WHEN** the walk ends
- **THEN** the pool is treated as exhausted without waiting for any background refresh
- **AND** the client receives the canonical `usage_limit_reached` rejection

#### Scenario: A transient walk-out is not exhaustion

- **GIVEN** a walk whose attempted accounts were excluded on transient evidence rather than exhaustion evidence
- **WHEN** the walk ends
- **THEN** the walk's own evidence does not prove exhaustion
- **AND** the terminal decision falls back to the persisted-state probe

#### Scenario: A message-derived usage limit counts as exhaustion evidence

- **GIVEN** an account excluded because its rejection's message proved the usage limit rather than its error code
- **WHEN** the walk tallies its evidence
- **THEN** that exclusion counts exactly as a coded `usage_limit_reached` exclusion does

### Requirement: Pool-walk termination consults the exhaustion probe

When an account walk ends without a served response, the client-visible failure MUST be decided from the bound that ended the walk, and MUST NOT be the first, the last, or an arbitrary per-account rejection chosen without that decision.

Two bounds answer without consulting the probe, because the pool's state is not what ended the walk: a `non_retryable` failure surfaces as itself, and an exhausted request budget yields `upstream_request_timeout` as "Streaming Responses requests use a bounded retry budget" requires. For every other bound the proxy MUST consult the pool-exhaustion probe at most once per request, using the same eligibility filtering ordinary selection applies, and MUST combine that answer with the walk's own evidence as required by "A walk proves exhaustion from its own attempts".

When the probe reports pool-wide usage exhaustion, the proxy MUST render the canonical usage-limit rejection defined by "Pool usage exhaustion is reported as a usage-limit error", including `error.resets_at` when an authoritative reset timestamp is available.

When neither the probe nor the walk's own evidence proves exhaustion — including the decline the probe returns for drain routing strategies — the proxy MUST return the preserved failure of the last attempted account, with its upstream status, error code, error body and retry hints unchanged.

A client that receives the canonical pool rejection MUST NOT be left without retry guidance: when no authoritative reset timestamp is available for `error.resets_at`, the response MUST carry a retry hint instead.

#### Scenario: Every account exhausted yields the canonical pool rejection

- **GIVEN** every selectable account rejects the request with a quota or usage-limit failure
- **WHEN** the walk ends
- **THEN** the probe is consulted exactly once
- **AND** the client receives HTTP `429` with `error.code = "usage_limit_reached"` and `error.resets_at` when a reset timestamp is known
- **AND** the client does not receive the first rejecting account's verbatim upstream body

#### Scenario: A transient walk-out preserves the last account failure

- **GIVEN** the accounts attempted during the walk failed with `retryable_transient` failures and the probe does not report usage exhaustion
- **WHEN** the walk ends
- **THEN** the client receives the last attempted account's failure with its status, code and body unchanged

#### Scenario: Drain strategies keep today's response

- **GIVEN** the configured routing strategy is a drain strategy, for which the probe declines to answer
- **WHEN** the walk ends
- **THEN** the client receives the preserved per-account failure exactly as it does today

## MODIFIED Requirements

### Requirement: Code-less upstream HTTP 429 rejections enter a short replica-local burst cooldown

When upstream answers a stream dispatch for a selected account with HTTP 429 whose error body carries no error code or type (normalized to `upstream_error`, classified `retryable_transient`), the proxy MUST, at the point where account health is written for that failure, record a replica-local per-account **burst cooldown** on the account's runtime state in addition to the existing transient error penalty. The cooldown deadline MUST be `now + clamp(retry_after, 5 s, 30 s)`, where `retry_after` is the upstream `Retry-After` value and a missing value applies 5 s; a rejection that arrives while a cooldown is already active MUST extend the deadline and MUST NOT shorten it. While the cooldown is active the account MUST be treated exactly as an account in overload soft backoff wherever a NEW account is chosen for a request — fresh (unbound) selection and the sticky path's fresh binding, reallocation, or fallback pick: it MUST be dropped from a candidate pool only while at least one other candidate remains, and when the configured strategy and budget gates select none of the remaining candidates, selection MUST run again over the full pool exactly as before. The cooldown MUST NOT engage the overload isolation stage, MUST NOT feed the overload rejection window, MUST NOT move an established sticky owner, a continuity owner, or a hard-affinity owner, MUST NOT write `RuntimeState.cooldown_until`, the persisted account status, `reset_at`, or `blocked_at`, MUST NOT change the per-account failure classification or the per-account health write, and MUST NOT be exposed as a new setting.

For an **owner-bound** request the cooldown MUST NOT change the status and body returned to the client: the original rejection is surfaced after the bounded same-account backoff. For a request that is **not** owner-bound the rejection MUST NOT be surfaced from this account at all while other candidates remain; the account is excluded and the walk continues, and the client-visible status and body are decided by "Pool-walk termination consults the exhaustion probe".

A 429 that carries a rate-limit or quota code (`rate_limit_exceeded`, `usage_limit_reached`) MUST keep the existing rate-limit handling and MUST NOT engage the burst cooldown. A code-less 429 whose message asserts the usage limit is classified `rate_limit` by "Usage-limit messages classify as account rate limits", and therefore also MUST NOT engage the burst cooldown. The proxy MUST log a warning when the cooldown engages, naming the account under the configured redaction policy, the applied cooldown seconds, and the upstream `Retry-After` value.

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

#### Scenario: An unbound burst rejection walks instead of surfacing

- **GIVEN** account A returns a code-less HTTP 429 for a request that is not owner-bound and account B is selectable
- **WHEN** the proxy evaluates the failover outcome
- **THEN** the burst cooldown is engaged for account A and account A is excluded for this request
- **AND** account B is attempted
- **AND** account A's 429 is not the client-visible response while any candidate remains
