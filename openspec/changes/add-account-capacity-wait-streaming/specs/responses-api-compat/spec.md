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

### Requirement: Streaming account-capacity waits keep clients alive
When a streaming Responses request waits for temporary account capacity to recover before account selection can continue, the proxy MUST emit downstream progress events during the wait. HTTP/SSE and HTTP bridge streams MUST emit `codex.keepalive` events with `status = "waiting_for_account_capacity"`, request id, elapsed wait seconds, and retry-after seconds when known. HTTP bridge streams MAY also emit `response.in_progress` to satisfy OpenAI Responses stream parsers before later terminal events. WebSocket clients MUST receive equivalent `codex.keepalive` JSON messages. These progress events MUST NOT expose account emails, API keys, raw affinity keys, prompt content, or request payloads.

#### Scenario: HTTP/SSE capacity wait emits keepalive
- **WHEN** `/v1/responses` streaming account selection can recover after a retry hint
- **THEN** the stream emits `codex.keepalive` with `status = "waiting_for_account_capacity"`
- **AND** includes the request id, waited seconds, and bounded retry-after seconds

#### Scenario: HTTP bridge capacity wait preserves parser progress
- **WHEN** an HTTP responses bridge request waits for session creation or account selection capacity
- **THEN** the bridge stream emits a capacity-wait keepalive
- **AND** emits OpenAI-compatible in-progress events when needed so downstream Responses stream parsers do not time out before the terminal response

#### Scenario: WebSocket capacity wait emits JSON keepalive
- **WHEN** a WebSocket Responses request waits for account capacity recovery
- **THEN** the downstream WebSocket receives a JSON `codex.keepalive` message with `status = "waiting_for_account_capacity"`
- **AND** the connection remains open until selection retries, the request budget expires, or the client disconnects
