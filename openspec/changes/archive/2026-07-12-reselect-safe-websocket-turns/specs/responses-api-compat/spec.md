# Responses API compatibility delta

## MODIFIED Requirements

### Requirement: WebSocket full-resend previous-response misses MUST retry without stale anchor

When a direct WebSocket `response.create` request includes both `previous_response_id` and a self-contained full resend payload, the service MUST retain a safe replay body without `previous_response_id`. If upstream
rejects the anchor with `previous_response_not_found` before
`response.created`, the service MUST reconnect and replay the retained full
payload as a fresh turn instead of forwarding the raw upstream invalid-request
error. A payload that only carries incremental tool outputs for tool calls that
are not also present in the same request is not self-contained and MUST NOT be
replayed as a fresh turn without `previous_response_id`.

For account switching caused by capability revalidation, quota, authentication,
or another pre-visible account-local failure, a client-owned
`previous_response_id` MUST remain owner-bound even when the request resembles
a full resend. The service MAY remove an anchor for account switching only when
the proxy injected that anchor after verifying retained continuity metadata and
captured an independently equivalent fresh request body. A verified replay MUST
exclude the failed owner, release any account-local create lease, and reallocate
sticky selection before reconnecting. It MUST NOT move if the retained fresh
body contains an account-scoped file reference.

#### Scenario: full-resend WebSocket follow-up loses just-completed anchor

- **WHEN** a WebSocket `/v1/responses` or `/backend-api/codex/responses`
  follow-up has `previous_response_id`
- **AND** the request payload also carries enough input to be treated as a full
  resend
- **AND** upstream emits `previous_response_not_found` before assigning a
  response id
- **THEN** the service reconnects the upstream WebSocket
- **AND** it replays the same request without `previous_response_id`
- **AND** the downstream client receives recovered response events rather than
  the raw upstream error

#### Scenario: output-only WebSocket tool delta is not replayed as a fresh turn

- **WHEN** a WebSocket follow-up has `previous_response_id`
- **AND** its input contains a tool output without the matching tool call
- **THEN** the service MUST NOT replay it as a fresh turn without the anchor
- **AND** it emits a retryable continuity failure when the owner cannot serve it

#### Scenario: client-owned full resend hits owner quota

- **WHEN** a client-owned previous-response request resembles a full resend
- **AND** its owner reports a pre-visible quota failure
- **THEN** the service keeps the request owner-bound
- **AND** it does not infer transcript completeness by stripping the anchor

#### Scenario: proxy-verified anchor hits owner quota

- **WHEN** the proxy injected a previous-response anchor after matching retained
  input fingerprints
- **AND** it retained the equivalent unanchored request body
- **AND** the owner reports a pre-visible quota failure
- **THEN** the service may reconnect through another eligible account using the
  retained fresh body
- **AND** the failed owner is excluded from replay selection
- **AND** its account-local create lease is released before replacement
  selection

#### Scenario: proxy-verified anchor hits owner refresh failure

- **WHEN** the proxy injected a previous-response anchor after matching retained
  input fingerprints
- **AND** it retained the equivalent unanchored request body
- **AND** the owner fails token refresh or upstream connection before any
  downstream-visible output
- **THEN** the service may remove the proxy-injected anchor
- **AND** replay the retained fresh body through another eligible account while
  excluding the failed owner

#### Scenario: client-owned continuation hits security authorization routing

- **WHEN** a client-owned previous-response request receives a pre-visible
  security-work authorization error
- **THEN** the service keeps the request owner-bound
- **AND** it does not strip the anchor to route through another account

#### Scenario: file-backed proxy-verified body hits security authorization routing

- **WHEN** a proxy-injected previous-response anchor has a retained fresh body
- **AND** that body contains `input_file.file_id`
- **AND** upstream returns a pre-visible security-work authorization error
- **THEN** the service keeps the request owner-bound
- **AND** it does not retry the retained body through a different security-work
  account

#### Scenario: verified fresh body contains an uploaded file

- **WHEN** a proxy-injected previous-response anchor has a retained fresh body
- **AND** that body contains `input_file.file_id`
- **THEN** the service does not remove the anchor for account switching
- **AND** it preserves the account that owns the uploaded file

### Requirement: Cross-transport full resends MUST require retained continuity proof

When an HTTP Responses request references a response completed on a direct WebSocket session, the service MUST remove the owner-scoped `previous_response_id` after a pre-visible owner failure only when the same
process retains session continuity metadata proving that the resent input
starts with the exact stored input prefix. The input MUST be self-contained for
tool semantics and MUST NOT contain an account-scoped file reference. Missing
or mismatched proof MUST keep the request owner-bound.

#### Scenario: verified WebSocket-to-HTTP image full resend

- **GIVEN** a direct WebSocket response completed with a stored input count and
  fingerprint for a named Codex session
- **AND** the next HTTP request in that session references the response
- **AND** its input begins with the exact stored prefix, includes each tool call
  before its output, and uses only inline image data
- **WHEN** the response owner is unavailable before visible output
- **THEN** the service may remove `previous_response_id`
- **AND** replay the full input through another eligible account

#### Scenario: trimmed HTTP bridge full resend hits owner quota

- **GIVEN** an HTTP bridge injected a durable anchor after proving and trimming
  the stored input prefix
- **AND** it retained the equivalent unanchored full request
- **WHEN** the owner reports a pre-visible quota failure
- **THEN** the bridge removes the proxy-injected anchor
- **AND** reconnects on another eligible account while excluding the failed
  owner

#### Scenario: trimmed HTTP bridge full resend stalls before response creation

- **GIVEN** an HTTP bridge injected a durable anchor after proving and trimming
  the stored input prefix
- **AND** it retained the equivalent unanchored full request
- **WHEN** the owner disconnects or stalls before `response.created`
- **THEN** the bridge removes the proxy-injected anchor
- **AND** reconnects on another eligible account while excluding the failed
  owner

#### Scenario: structural full resend lacks retained proof

- **GIVEN** an HTTP request carries `previous_response_id` and multiple input
  items
- **AND** no matching session continuity fingerprint is retained
- **WHEN** the owner is unavailable
- **THEN** the service fails closed
- **AND** it does not infer completeness from input length or shape alone

#### Scenario: account-scoped file reference cannot move

- **GIVEN** an otherwise verified cross-transport full resend contains an
  `input_file.file_id` or account-scoped image file reference
- **WHEN** the owner is unavailable
- **THEN** the service keeps the request owner-bound

## ADDED Requirements

### Requirement: Safe HTTP bridge pre-created retries MUST avoid stalled owners

When an unanchored HTTP bridge request is retried before visible output, the service MUST exclude the account that failed to create the response when the
request has no account-scoped file requirement. A request with an account-
scoped file requirement MUST remain bound to its file owner.

#### Scenario: unanchored bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request is safely replayable before
  `response.created`
- **AND** it has no account-scoped file requirement
- **THEN** the bridge excludes the stalled account before reconnecting

#### Scenario: file-backed bridge request stalls before response creation

- **WHEN** an unanchored HTTP bridge request requires its file-owner account
- **AND** it is retried before `response.created`
- **THEN** the bridge does not exclude or clear the required file owner

## MODIFIED Requirements

### Requirement: Hard continuity owner lookup fails closed

When a request depends on hard continuity ownership, the service MUST fail
closed if owner or ring lookup errors prevent safe pinning. The service MUST NOT
continue with account selection that bypasses hard owner enforcement. A direct
WebSocket continuation already attached to its required open owner socket MUST
NOT be failed solely because a new per-turn selection attempt temporarily
excludes that owner.

#### Scenario: websocket previous-response owner lookup errors

- **WHEN** a websocket or HTTP fallback follow-up includes
  `previous_response_id`
- **AND** owner lookup errors prevent determining the required owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not continue on an unpinned account

#### Scenario: bridge owner or ring lookup errors for hard continuity keys

- **WHEN** an HTTP bridge request uses a hard continuity key such as turn-state,
  explicit session affinity, or `previous_response_id`
- **AND** owner or ring lookup errors prevent proving the correct bridge owner
- **THEN** the service returns a retryable OpenAI-format error
- **AND** it does not create or recover a local bridge session on the current
  replica

#### Scenario: required owner differs from the open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to an owner different from the
  currently open upstream account
- **THEN** the service retires the current upstream socket
- **AND** reconnects the unchanged anchored request to the required owner
- **AND** it does not forward any `x-codex-turn-state` associated with the
  retired account, whether supplied by the client or learned upstream

#### Scenario: required owner matches the healthy open WebSocket account

- **WHEN** a direct WebSocket follow-up resolves to the currently open owner
- **THEN** the service sends it on that socket without a new selector-based
  eligibility check
