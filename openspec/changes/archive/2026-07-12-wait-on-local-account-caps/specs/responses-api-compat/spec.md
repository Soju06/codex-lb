## MODIFIED Requirements

### Requirement: Streaming Responses requests use a bounded retry budget
When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, account-capacity recovery waits, and upstream stream attempts. Each upstream stream attempt MUST clamp its connect timeout, idle timeout, and total request timeout to the remaining request budget.

#### Scenario: Remaining budget constrains all stream attempt timeouts
- **WHEN** account selection, account-capacity recovery, or token refresh leaves only part of the request budget available before a stream attempt starts
- **THEN** the proxy limits the upstream connect timeout, SSE idle timeout, and upstream request total timeout to that same remaining budget
- **AND** the client receives `response.failed` with `upstream_request_timeout` once that budget is exhausted instead of waiting through the full configured stream windows

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
