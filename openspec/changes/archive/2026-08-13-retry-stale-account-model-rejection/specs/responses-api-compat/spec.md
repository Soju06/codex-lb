# responses-api-compat Delta Specification

## ADDED Requirements

### Requirement: Pre-acceptance account-model rejections fail over safely

When upstream rejects a Responses request with `invalid_request_error` and the exact message `The '<model>' model is not supported when using Codex with a ChatGPT account.` before accepting the response, the proxy MUST classify the failure internally as `account_model_unsupported`. The quoted model MUST match
the requested model. For native WebSocket, HTTP responses bridge, and raw
HTTP/SSE transports, the proxy MUST make at most one transparent attempt on a
different account that advertises the same model, provided the request can move
without violating continuation or uploaded-file ownership. The proxy MUST
exclude the rejecting account only for that request and MUST NOT record an
account-health penalty for this rejection.

The proxy MUST NOT replay after any response id recognized in an upstream payload,
including a `response.failed` payload that carries `response.id` even when
`response.created` was not observed or an `error` payload with top-level
`response_id`, a nonterminal `response.*`
event, downstream sequence/output, another pending request on the shared
socket, or an earlier replay. If no compatible replacement is available, or
the request is account-bound, the proxy MUST preserve the original upstream
400 error instead of replacing it with `no_accounts`, `stream_incomplete`, or
another proxy-generated failure.

#### Scenario: stale model route retries another advertising account

- **GIVEN** two accounts advertise the requested model in the current routing snapshot
- **AND** upstream rejects the first account with the exact account/model unsupported envelope before `response.created`
- **WHEN** the request has no hard account or uploaded-file binding
- **THEN** the proxy excludes the first account for this request and retries once on the second account
- **AND** it forwards only the replacement attempt's response events downstream
- **AND** it does not penalize the first account's global health

#### Scenario: no replacement preserves the upstream rejection

- **GIVEN** upstream rejects a pre-acceptance request with the exact account/model unsupported envelope
- **AND** no other compatible account is available
- **WHEN** transparent failover cannot select a replacement
- **THEN** the client receives the original HTTP 400 `invalid_request_error`
- **AND** the error is not rewritten to `no_accounts`, `stream_incomplete`, or HTTP 502

#### Scenario: selected replacement failure is surfaced

- **GIVEN** upstream rejects a pre-acceptance request with the exact account/model unsupported envelope
- **AND** the proxy selects a different compatible replacement account
- **WHEN** that replacement attempt fails before acceptance
- **THEN** the client receives the replacement attempt's failure
- **AND** the skipped account's original HTTP 400 is not used as a fallback
- **AND** the proxy does not select a third account after a retryable replacement
  refresh, transport, or server failure

#### Scenario: failed bridge replacement retires without restoring rejected metadata

- **GIVEN** an HTTP responses bridge reconnect has selected and installed a
  replacement account after an account/model rejection
- **WHEN** replacement response-create lease acquisition or request send fails
- **THEN** the proxy forwards the replacement failure and retires that bridge
  session after draining the rejected request
- **AND** it does not restore the rejected account's turn state or headers onto
  the replacement socket

#### Scenario: accepted or visible request is never replayed

- **WHEN** the account/model unsupported envelope arrives after a response id, a nonterminal response event, downstream sequence/output, or an earlier replay
- **THEN** the proxy does not transparently replay the request on another account

#### Scenario: account-bound request is never migrated

- **WHEN** a rejected request depends on an account-scoped uploaded file or an owner-bound continuation without a verified self-contained fresh replay body
- **THEN** the proxy does not move the request to another account
- **AND** it preserves the original upstream rejection
