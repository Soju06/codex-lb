# account-routing Delta

## ADDED Requirements

### Requirement: Upstream usage-limit rejections are recognized in every delivery form

Upstream asserts that a selected account's usage limit is spent in more than one delivery form: as an HTTP error body, which carries a status and usually an error code, and as a serialized `response.failed` terminal frame, which carries no HTTP status and MAY carry no error code at all. The proxy MUST recognize that rejection from the evidence the delivery form actually carries, and the match MUST NOT depend on the HTTP status, because the status-bearing and status-less forms are the same rejection.

When an envelope carries the code `usage_limit_reached`, that code alone MUST prove the rejection. Otherwise the proxy MUST read the account's usage limit from the error message, matched after folding each run of non-alphanumeric characters in the lowercased message to a single space, so that the same sentence still matches with a straight or curly apostrophe, with a hyphen joining "usage" and "limit", or wrapped across a line break. The message MUST be allowed to prove the rejection only for the one normalized code that carries no classification decision of its own: `upstream_error`, which is what a missing code normalizes to. Any other code MUST keep the classification its code chose; a message MUST NOT reverse a decision a code already made. `invalid_request_error` MUST NOT be read this way even though upstream also reuses it for rejections it has no code for: it is upstream's catch-all for request-shaped failures, whose message can quote request content back, and the HTTP paths forward their status to the account-health write as evidence only rather than as the status the classifier reads, so nothing downstream could separate a real rejection from an echo. Falsely benching a serving account is a worse outcome than the miss that exclusion leaves open. Plain `rate_limit_exceeded` throttling MUST NOT be read as a usage-limit rejection: it asserts that the request arrived too fast, not that the subscription window is exhausted.

A rejection proven this way MUST be classified `rate_limit` rather than `retryable_transient`, so the remedy is to bench the account rather than to wait it out on itself.

Both streaming decisions that read a pre-visible terminal frame MUST take their answer from this recognition rather than from a code table alone:

- the retry decision, so that a frame proving the usage limit moves the request to a sibling account exactly as the same rejection delivered as an HTTP body does, instead of being surfaced on the account that just said it was spent;
- the account-health write, so that the account is benched exactly as the coded frame benches it. This MUST hold for the last account of a walk, which is reached with retry no longer permitted and whose frame is therefore terminal rather than retried, and for a frame that arrives after a lifecycle event is already downstream, where the request is committed to the account and its health is the only remedy left for the next request.

The WebSocket transport MUST reach the same answer from the same recognition, for both its account-health write and its transparent-replay decision. A WebSocket terminal frame proven this way MUST answer under the `usage_limit_reached` code, so that an owner-pinned turn, the socket's retirement, and the account's health write all take the path the coded form takes.

Recognizing the rejection MUST NOT change the error code, the error message, or the response body surfaced to the client, and MUST NOT widen any other rejection: a code-less frame whose message does not assert the account's usage limit MUST keep its existing terminal, retry, and account-health behaviour.

#### Scenario: A code-less terminal frame walks the pool like the HTTP body

- **GIVEN** account A is selected, account B is selectable, and nothing is downstream yet
- **WHEN** upstream answers account A with a `response.failed` frame carrying no error code whose message asserts the usage limit has been reached
- **THEN** the proxy leaves account A, dispatches to account B, and the client sees no `response.failed`
- **AND** account A is left rate-limited

#### Scenario: The last account of a walk is still benched

- **GIVEN** every account in the pool answers with a usage-limit `response.failed` frame
- **WHEN** the walk reaches the last account, where retry is no longer permitted and the frame is terminal
- **THEN** that account's health is recorded as rate-limited
- **AND** the result is the same whether or not upstream attached the `usage_limit_reached` code

#### Scenario: A frame after a visible event still records account health

- **GIVEN** a lifecycle event has already been relayed downstream for the selected account
- **WHEN** upstream then sends a code-less `response.failed` frame asserting the usage limit
- **THEN** the proxy surfaces that frame unchanged, because the request is committed to this account
- **AND** it still records the account as rate-limited for the next request

#### Scenario: The WebSocket transport benches and releases the same turn

- **GIVEN** a WebSocket turn on a selected account
- **WHEN** upstream sends a `response.failed` frame carrying no error code whose message asserts the usage limit
- **THEN** the account's health is recorded under `usage_limit_reached`, the upstream socket is retired, and the turn is released for replay
- **AND** the outcome is the same as for the identical frame carrying the code

#### Scenario: An unrelated code-less frame is unaffected

- **GIVEN** account A is selected and a sibling is selectable
- **WHEN** upstream answers with a `response.failed` frame carrying no error code whose message asserts something other than the account's usage limit
- **THEN** the proxy surfaces that frame on account A without walking the pool
- **AND** it writes no account-health penalty for it

#### Scenario: A coded envelope keeps what its code decided

- **GIVEN** an upstream failure whose code is `overloaded_error`, `server_error`, or any other code that already carries a classification
- **WHEN** its message happens to contain the usage-limit wording
- **THEN** the classification stays the one the code chose

#### Scenario: The request-shaped catch-all is never read from its message

- **GIVEN** an upstream failure normalized to `invalid_request_error`
- **WHEN** its message contains the usage-limit wording
- **THEN** the proxy does not read it as a usage-limit rejection
- **AND** the account's health is left exactly as it was before

## MODIFIED Requirements

### Requirement: Code-less upstream HTTP 429 rejections enter a short replica-local burst cooldown

When upstream answers a stream dispatch for a selected account with HTTP 429 whose error body carries no error code or type (normalized to `upstream_error`, classified `retryable_transient`), the proxy MUST, at the point where account health is written for that failure, record a replica-local per-account **burst cooldown** on the account's runtime state in addition to the existing transient error penalty. The cooldown deadline MUST be `now + clamp(retry_after, 5 s, 30 s)`, where `retry_after` is the upstream `Retry-After` value and a missing value applies 5 s; a rejection that arrives while a cooldown is already active MUST extend the deadline and MUST NOT shorten it. While the cooldown is active the account MUST be treated exactly as an account in overload soft backoff wherever a NEW account is chosen for a request — fresh (unbound) selection and the sticky path's fresh binding, reallocation, or fallback pick: it MUST be dropped from a candidate pool only while at least one other candidate remains, and when the configured strategy and budget gates select none of the remaining candidates, selection MUST run again over the full pool exactly as before. The cooldown MUST NOT engage the overload isolation stage, MUST NOT feed the overload rejection window, MUST NOT move an established sticky owner, a continuity owner, or a hard-affinity owner, MUST NOT write `RuntimeState.cooldown_until`, the persisted account status, `reset_at`, or `blocked_at`, MUST NOT change the failure classification or the status and body returned to the client, and MUST NOT be exposed as a new setting. A 429 whose rejection is a rate-limit or quota one MUST keep the existing rate-limit handling and MUST NOT engage the burst cooldown; that covers both a 429 carrying `rate_limit_exceeded`, `usage_limit_reached`, or a quota code, and a code-less 429 whose message proves the account's usage limit is spent, since an account with nothing left to give cannot be waited out on itself. The proxy MUST log a warning when the cooldown engages, naming the account under the configured redaction policy, the applied cooldown seconds, and the upstream `Retry-After` value.


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

#### Scenario: A code-less 429 proving the usage limit is not a burst

- **GIVEN** upstream answers a stream dispatch with HTTP 429 carrying no error code whose message asserts the account's usage limit has been reached
- **WHEN** the proxy classifies and writes account health for it
- **THEN** it applies the rate-limit handling rather than the burst cooldown
- **AND** the request is not made to wait out a bounded same-account backoff on an account that is out of quota
