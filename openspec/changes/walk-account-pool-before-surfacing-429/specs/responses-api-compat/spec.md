# responses-api-compat Delta

## ADDED Requirements

### Requirement: Usage-limit messages classify as account rate limits

When an upstream error envelope carries a message asserting that the account's usage limit has been reached, and its normalized error code is either the `upstream_error` value a missing code normalizes to or `invalid_request_error`, the proxy MUST classify the failure `rate_limit`. The override is deliberately limited to those two codes: a code that already carries its own classification decision — a rate-limit or quota code, `overloaded_error`, or any other transient code — keeps it, so this requirement cannot silently reverse "Model-capacity messages are retryable transient failures" or the rule that `overloaded_error` stays retryable regardless of status. Such a failure MUST NOT be classified `retryable_transient`, MUST NOT be treated as a burst rejection, and MUST NOT be answered with same-account backoff: the account is out of quota, so waiting on it cannot succeed.

This requirement does not change the classification of an envelope that already carries a quota or rate-limit code; that case keeps the stronger classification it has today.

Reclassification MUST NOT remove client-visible retry guidance. A rejection that would previously have surfaced with a `Retry-After` hint MUST still carry one, or an `error.resets_at`, when it reaches the client.

Message matching MUST be punctuation-insensitive and MUST NOT depend on the HTTP status, because upstream delivers this message both as an HTTP body and as a serialized `response.failed` frame that carries no status.

#### Scenario: Code-less usage-limit 429 rotates instead of backing off

- **WHEN** upstream answers with HTTP `429` whose body carries no error code and whose message asserts the usage limit has been reached
- **THEN** `classify_upstream_failure` returns `failure_class = "rate_limit"`
- **AND** the failure is not a burst rejection
- **AND** an unbound request excludes the account and continues the pool walk instead of waiting 1 s / 2 s / 4 s on it

#### Scenario: Usage-limit message under a non-rate-limit code still rotates

- **WHEN** upstream returns an envelope whose normalized error code is `invalid_request_error` and whose message asserts the usage limit has been reached
- **THEN** `classify_upstream_failure` returns `failure_class = "rate_limit"`
- **AND** the account's rate-limit health penalty is recorded as for any other rate-limit rejection

#### Scenario: A coded rate-limit envelope is unaffected

- **WHEN** upstream returns an envelope whose normalized error code is `rate_limit_exceeded` or `usage_limit_reached`
- **THEN** the classification is exactly what it is today
- **AND** this requirement adds no message-based reclassification on top of it

### Requirement: Model-capacity rejections do not exclude the account from the pool walk

A rejection whose message says the selected model is at capacity describes the requested model, not the selected account. When the proxy decides whether a pre-visible failure justifies excluding the selected account for the remainder of the request, a model-capacity rejection MUST NOT justify that exclusion — but only for a failure class whose account-health write leaves the account selectable.

A rejection that benches the account is the opposite case and MUST still exclude it. A `rate_limit` or `quota` classification persists a status and a reset deadline, so selection will not offer that account again regardless of what the walk decides; reporting it as "do not exclude" would hand the walk a candidate selection cannot use, and would make the two answers contradict each other for the one envelope that carries both a benching code and the capacity message. The health write is the authority on whether an account is benched; the capacity carve-out applies to the walkable classes it leaves alone.

The account-health write, the persisted status, the reset deadline and the model-capacity replay wait are unchanged by this requirement: it governs account selection only.

The exclusion answer the classifier reports MUST be the selection predicate, not an exhaustion predicate. It is true for every pre-visible failure the walk may move away from — `rate_limit`, `quota` and `retryable_transient` alike, which includes the code-less burst 429 that "An unbound burst rejection walks instead of surfacing" requires to be excluded — and false only when the rejection is a model-capacity one. A field that answers "was this account exhaustion" instead collapses the burst rejection and the capacity rejection to the same value and cannot drive the walk. A message that asserts the usage limit MUST take precedence over a model-capacity match when both appear in one envelope, because the usage limit is account-scoped.

#### Scenario: Capacity on a walkable class does not rotate the pool

- **GIVEN** a pool of several selectable accounts and a request that is not owner-bound
- **WHEN** the selected account returns a `retryable_transient` rejection whose message says the selected model is at capacity
- **THEN** the account is not excluded, and the pool is not walked for a condition no account can serve

#### Scenario: A benching code excludes even under a capacity message

- **WHEN** the selected account returns an envelope whose code is `rate_limit_exceeded` or a quota code, and whose message also says the selected model is at capacity
- **THEN** the account keeps the rate-limit or quota health classification it has today, which benches it
- **AND** it is excluded for the remainder of the request, because selection will not offer a benched account and the two answers must not contradict each other

#### Scenario: Usage limit wins when both messages appear

- **WHEN** an envelope's message asserts both the model capacity and that the usage limit has been reached
- **THEN** the rejection is treated as account exhaustion
- **AND** the account is excluded for the remainder of the request

## MODIFIED Requirements

### Requirement: Streaming Responses requests use a bounded retry budget

When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, account-capacity recovery waits, and upstream stream attempts. Each upstream stream attempt MUST clamp its connect timeout, idle timeout, and total request timeout to the remaining request budget.

The number of accounts a request may attempt MUST NOT be a fixed per-transport constant. It MUST be bounded by the remaining request budget, by a fixed runaway ceiling on account attempts within one request, and by the monotone growth of the request-scoped excluded-account set, as required by "A single account's rejection is not the pool's rejection". The runaway ceiling MUST NOT be an operator setting.

#### Scenario: Remaining budget constrains all stream attempt timeouts
- **WHEN** account selection, account-capacity recovery, or token refresh leaves only part of the request budget available before a stream attempt starts
- **THEN** the proxy limits the upstream connect timeout, SSE idle timeout, and upstream request total timeout to that same remaining budget
- **AND** the client receives `response.failed` with `upstream_request_timeout` once that budget is exhausted instead of waiting through the full configured stream windows

#### Scenario: Budget exhaustion during a walk ends the walk
- **WHEN** a request has walked several accounts and the remaining request budget reaches zero before an account serves it
- **THEN** the proxy stops attempting further accounts
- **AND** the client receives `response.failed` with `upstream_request_timeout` rather than a usage-limit rejection

#### Scenario: Forced refresh retry recomputes all attempt timeouts
- **WHEN** a first stream attempt fails with an authentication error that triggers a forced token refresh and retry
- **THEN** the proxy recomputes the remaining request budget after the refresh
- **AND** the retry attempt reapplies connect, idle, and total timeout limits from that recomputed budget

#### Scenario: Recoverable account-capacity wait is bounded by the request budget
- **WHEN** account selection reports a recoverable retry hint such as temporary rate-limit or stream-capacity exhaustion
- **AND** the streaming request still has remaining request budget
- **THEN** the proxy may wait for at most the smaller of the recovery hint and the remaining request budget before retrying selection
- **AND** if the budget is exhausted before an account becomes available, the request fails through the normal no-account or rate-limit error path instead of starting a fresh full-budget wait

#### Scenario: Local balancer rate-limit exhaustion is not treated as recoverable capacity
- **WHEN** account selection reports the local balancer message `Rate limit exceeded. Try again in Ns`
- **AND** the selection result is a local no-account failure with `no_accounts` or no explicit error code
- **THEN** the proxy does not enter an account-capacity recovery wait from that local retry hint
- **AND** the request returns through the normal no-account or rate-limit error path instead of repeatedly retrying the same local selection failure

#### Scenario: Local account cap selection waits instead of failing immediately
- **WHEN** account selection for a streaming Responses request fails locally with `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy treats the condition as a recoverable account-capacity wait within the request budget
- **AND** it retries account selection after the bounded wait instead of returning an immediate 429
- **AND** permanent `no_accounts` failures remain non-waitable unless they carry a distinct recoverable capacity or upstream quota signal

#### Scenario: Post-selection response-create capacity preserves routing invariants
- **WHEN** a selected account reaches `account_response_create_cap` before downstream output is visible
- **THEN** an unpinned request MUST prefer an eligible alternate account before waiting
- **AND** an owner-bound, file-pinned, or otherwise same-account retry MUST keep or reacquire its stream lease while waiting within the original request budget
- **AND** the same behavior applies after a forced token refresh

#### Scenario: SDK-contract propagated startup errors remain observable
- **WHEN** a route requests HTTP error propagation, enforces the OpenAI SDK stream contract, and waits for local account capacity before startup
- **THEN** the route MUST perform the bounded recovery wait instead of raising the first cap error immediately
- **AND** it MUST NOT emit an account-capacity keepalive before startup succeeds, so a terminal startup error can still use the route's structured error path

#### Scenario: Existing HTTP bridge session waits on submit capacity
- **WHEN** HTTP bridge session submission reaches `account_response_create_cap`
- **THEN** a hard-affinity or file-pinned request MUST wait and retry submission within the bridge request budget
- **AND** a soft-affinity request MUST retain its existing alternate-session reroute behavior before waiting on the saturated session

#### Scenario: WebSocket account selection waits on local caps
- **WHEN** downstream WebSocket account selection returns `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy MUST emit a `codex.keepalive` with status `waiting_for_account_capacity`
- **AND** retry selection within the original WebSocket request budget
- **AND** return the original local-cap error if that budget is already exhausted
