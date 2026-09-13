# account-routing Delta

## ADDED Requirements

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
