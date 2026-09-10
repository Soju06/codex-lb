# sticky-session-operations Delta

## ADDED Requirements

### Requirement: Owner-bound requests retry the same owner after an upstream burst rejection

When a pre-visible upstream failure on the HTTP stream transport is a code-less HTTP 429 (classified `retryable_transient` with HTTP status 429) and the request is **owner-bound** — it cannot be dispatched to another account because its payload replay is bound to the failed account (input items that are not account-neutral, such as `reasoning`), because a required preferred account, a file-pin owner, or a turn-state owner names that account, or because single-account routing is configured — the failover decision MUST be `retry_same_account` while a same-account retry is available and `surface` otherwise; it MUST NOT be `failover_next`, since an owner-bound request can never cross accounts. A same-account retry is available only while fewer than 3 same-account burst retries have been made for that account within the request, the request's remaining startup budget is positive, and no downstream-visible output has been emitted. For each `retry_same_account` the proxy MUST keep the account selectable for the request (no exclusion, replay binding kept), engage the `account-routing` burst cooldown for the account immediately (replica-local runtime state only, so other requests are steered away during the burst whether or not the stream is keyed), and wait `min(10 s, max(retry_after, 1 s * 2^(n-1)))` for retry `n` (1 s, 2 s, 4 s without an upstream `Retry-After`; the upstream value is a floor) through the propagated startup wait, so the HTTP response status and headers are not committed during the wait; on a route that propagates HTTP errors the wait MUST NOT emit keepalive frames (a frame would commit a 200/SSE response), and the startup probe MUST keep holding the headers across consecutive waits. The wait MUST be bounded by the remaining request budget and MUST use the stream scheduler and clock seams rather than raw sleeps. A same-account retry MUST NOT record a transient error penalty against the owner: the penalty is written exactly once, when the failure is finally surfaced, so a bursting owner never enters selection error backoff and a successful retry is never followed by a deferred penalty. On the pre-visible path the stream lease is kept across the retry (the redispatch stays inside the admitted slot); on the post-refresh path the lease is released before the same owner is re-selected. When the retry count is exhausted or the budget is spent, the proxy MUST surface the original upstream 429 status and body, and the surfaced response MUST carry a `Retry-After` header equal to the upstream value when present and `5` otherwise. The `Failover decision` log line MUST report the action that is actually executed. A request that is not owner-bound MUST keep the existing `failover_next` handling. The same rule MUST apply to the failover decision taken after a 401 token refresh.

#### Scenario: Same owner is retried after a bounded backoff while headers are held

- **GIVEN** a `prompt_cache` session pinned to account A whose payload carries `reasoning` items
- **AND** upstream answers the first dispatch with a code-less HTTP 429 and no `Retry-After`
- **WHEN** the proxy handles the failure before any downstream-visible output
- **THEN** the failover decision is `retry_same_account`
- **AND** the proxy waits 1 second without committing the HTTP response status
- **AND** the request is dispatched again to account A and, when upstream now accepts it, the client receives the normal response

#### Scenario: Retries are bounded and the original 429 is surfaced with Retry-After

- **GIVEN** an owner-bound request from a native Codex client (HTTP errors propagate, no OpenAI SDK contract) to account A
- **AND** upstream answers four consecutive dispatches with a code-less HTTP 429 and no `Retry-After`
- **WHEN** the proxy handles the fourth rejection
- **THEN** the proxy has waited 1, 2, and 4 seconds between dispatches without committing the HTTP response
- **AND** the client receives HTTP 429 with the original upstream body and `Retry-After: 5`
- **AND** the `Failover decision` log reports `retry_same_account` three times and then `surface`
- **AND** exactly one transient error penalty is recorded for account A

#### Scenario: Same-account retry leaves no penalty on a successful owner

- **GIVEN** an owner-bound request on an API key with a usage reservation
- **AND** upstream answers the first dispatch with a code-less HTTP 429 and the retry succeeds
- **WHEN** the stream completes
- **THEN** the burst cooldown for account A was engaged at rejection time, before the backoff wait
- **AND** no transient error penalty is recorded for account A after the stream's success
- **AND** the cooldown deadline is not extended when the stream settles

#### Scenario: Single-account routing retries the selected account

- **GIVEN** single-account routing is configured for account A and an account-neutral payload
- **WHEN** account A returns a code-less HTTP 429 before any downstream-visible output
- **THEN** the failover decision is `retry_same_account`, never `failover_next`
- **AND** account A is dispatched again after the backoff

#### Scenario: Post-refresh burst rejection retries the same owner

- **GIVEN** an owner-bound request whose first dispatch fails with HTTP 401 and the token refresh succeeds
- **AND** the redispatch returns a code-less HTTP 429
- **WHEN** the proxy handles the post-refresh failure
- **THEN** the `Failover decision` log reports `phase=post_refresh` and `retry_same_account`
- **AND** the same owner is re-selected and dispatched after the backoff
- **AND** if the owner cannot be re-selected, the surfaced 429 carries `Retry-After`

#### Scenario: Upstream Retry-After floors the wait and the budget bounds it

- **GIVEN** an owner-bound request whose first rejection carries `Retry-After: 3`
- **WHEN** the same-account retries are scheduled
- **THEN** the waits are 3, 3, and 4 seconds, each no longer than the request's remaining startup budget
- **AND** the surfaced 429, if retries are exhausted, carries `Retry-After: 3`
- **AND** a request whose remaining budget is exhausted surfaces the original 429 without further retries

#### Scenario: No cross-account reroute for an owner-bound request

- **GIVEN** an owner-bound request to account A and a selectable sibling B
- **WHEN** account A returns a code-less HTTP 429 and retries are exhausted
- **THEN** no dispatch is made to account B
- **AND** the replay binding to account A is preserved throughout
- **AND** the `Failover decision` log never reports `failover_next` for this request

#### Scenario: Downstream-visible output is never replayed

- **GIVEN** an owner-bound request that has already emitted a downstream-visible event
- **WHEN** upstream then fails the stream with HTTP 429
- **THEN** the failover decision is `surface`
- **AND** no same-account retry is attempted

#### Scenario: Movable request keeps failing over

- **GIVEN** a request whose payload is account-neutral and that carries no owner-binding continuity source
- **WHEN** account A returns a code-less HTTP 429 before any downstream-visible output
- **THEN** the failover decision is `failover_next`
- **AND** the next selection excludes account A and, while another candidate exists, the burst cooldown steers it away from A as well
