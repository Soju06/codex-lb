## ADDED Requirements

### Requirement: Native Codex stream quotas describe the pool

Native Codex Responses SSE and WebSocket egress MUST NOT forward an account's
raw `codex.rate_limits` payload. Default-family events MUST be rebuilt using
the pooled downstream quota-header snapshot, including its normalized window
durations, reset timestamps, and credits. HTTP SSE MUST use its response's
quota-header snapshot; WebSocket egress MUST use the existing aggregate cache.
Source-routed SSE MUST follow the same rule. The service MUST suppress quota
events when no aggregate window is available and MUST NOT substitute a global
aggregate for model-specific or otherwise unrepresented limit families.
Projection MUST happen after original upstream usage ingestion and MUST NOT
change normal response lifecycle events or account attribution. Failure to
read quota metadata on an established WebSocket MUST suppress the quota event
without interrupting inference; cancellation MUST still propagate.

#### Scenario: A nearly exhausted account serves a pool with capacity

- **GIVEN** the selected account reports 97% weekly usage and pooled downstream headers report 66.5%
- **WHEN** native Codex receives the Responses stream
- **THEN** its default quota event reports 66.5% weekly usage and the pooled reset timestamp
- **AND** the event contains no account-specific plan, credits, or quota metadata

#### Scenario: Unrepresented quota events cannot overwrite the pool

- **WHEN** an upstream quota event belongs to a model-specific family or no pooled window is known
- **THEN** the downstream stream omits that quota event
- **AND** response deltas and terminal events remain deliverable

#### Scenario: A quota cache read fails on an established WebSocket

- **WHEN** pooled quota metadata cannot be loaded while handling a quota event
- **THEN** the event is omitted without forwarding account-specific values
- **AND** the next response event can still be delivered

## MODIFIED Requirements

### Requirement: Public /v1 responses SSE stream emits only OpenAI Responses contract events

When serving streaming `POST /v1/responses`, the service MUST forward a
string-valued event type only when it is exactly `error` or begins with
`response.`. Other string-valued event types MUST be dropped before they reach
the public stream. OpenAI-shaped backend requests with public contract
enforcement enabled MUST follow the same filtering rule. Native Codex requests
with public contract enforcement disabled MUST retain upstream vendor events,
except `codex.rate_limits`, which MUST follow the pooled quota projection and
API-key quota privacy requirements.

#### Scenario: Codex-internal rate-limit event is dropped before response.created

- **WHEN** upstream emits `codex.rate_limits` before `response.created` for a streaming `/v1/responses` request
- **THEN** the public stream MUST NOT contain `codex.rate_limits`
- **AND** its first event MUST be `response.created`

#### Scenario: Timing diagnostics are filtered without losing text or completion

- **WHEN** upstream emits `responsesapi.websocket_timing` before, between, or after standard response events
- **THEN** a public-contract stream MUST NOT contain that diagnostic
- **AND** standard text deltas and completion events MUST remain in order

#### Scenario: OpenAI-shaped backend request filters vendor events

- **WHEN** an OpenAI-shaped `/backend-api/codex/responses` request enables public contract enforcement
- **THEN** its response stream MUST apply the public event-family filtering rule

#### Scenario: Codex-internal events on the Codex CLI route are preserved

- **WHEN** a native `/backend-api/codex/responses` request disables public contract enforcement
- **THEN** the response stream MUST retain `responsesapi.websocket_timing` in upstream order
- **AND** default `codex.rate_limits` events MUST contain the visible pooled quota snapshot or be omitted when no snapshot is available
