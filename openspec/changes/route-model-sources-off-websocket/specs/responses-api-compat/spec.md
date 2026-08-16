## ADDED Requirements

### Requirement: Source-owned models are not served over the WebSocket transport

Model sources are reachable only from the HTTP request path. When a WebSocket
Responses session requests a model that resolves to an enabled,
Responses-capable OpenAI-compatible model source, the system SHALL NOT dispatch
the request to a subscription account.

The check SHALL be applied on the connect path before account selection, and
SHALL also be applied to every prepared `response.create`, so that a turn which
switches to a source-owned model on an already-open subscription upstream is
also rejected instead of being forwarded.

Both checks SHALL evaluate the client's raw requested model, captured before
API-key enforcement normalizes model aliases (for example `gpt-5-high` to
`gpt-5`), alongside the normalized model — the same candidate list the HTTP
handlers build from `raw_source_model`, including substituting the API key's
`enforced_model` and, when fast mode is prohibited and the raw model is a
fast-mode alias, replacing the raw candidate with the normalized model. A
source that exposes an alias-named model MUST be matched on the WebSocket
transport whenever the HTTP path would route to it.

Both checks SHALL apply only to requests that are eligible for model-source
routing on the HTTP request path, judged on the full client input before any
WebSocket-specific trimming or anchor injection. A request whose input ends
with a terminal `compaction_trigger` item, or that references uploaded files
(`input_file` / file-backed `input_image` items), is excluded from source
routing over HTTP — the former is served by the upstream compact flow on the
turn's owner account, the latter is pinned to the subscription account that
received the upload — and MUST NOT be failed by either WebSocket guard even
when its model also resolves to an enabled source. Such requests proceed to
subscription account selection and the owner-routing rules, exactly as they
would after the HTTP route skips source selection. A malformed compaction
trigger (repeated, or not the final top-level input item) SHALL keep the
guards active: the HTTP route rejects that payload with a 400, the WebSocket
path forwards it verbatim, and the exclusion changes neither.

Both failures MUST use error code `model_source_requires_http_transport`. On the
connect path the failure MUST be emitted as a service-level connect failure
(HTTP status `503`), so that Codex clients fall back to the HTTP transport,
where source routing is applied. For a prepared `response.create` on an
established session the failure MUST be emitted as a terminal error for that
turn, and any usage reservation held for the turn MUST be released.

When source resolution is unavailable, the WebSocket transport MUST fall back to
subscription account selection rather than failing the request. The resolution
runs after a turn's usage reservation is acquired but before it is registered
for cleanup, so a propagating failure would end the session and strand the
reservation; the degraded behaviour is the pre-change one, where the
subscription upstream rejects the model. This applies to the WebSocket transport
only — the HTTP request path MUST continue to surface resolution failures, since
silently routing source traffic to a subscription account would be worse there.

#### Scenario: Source-owned model over WebSocket fails the connect

- **GIVEN** an enabled OpenAI-compatible model source exposes model `m` with Responses support
- **WHEN** a client opens a WebSocket Responses session requesting model `m`
- **THEN** the system fails the connect with error code `model_source_requires_http_transport`
- **AND** no subscription account is selected for the request

#### Scenario: Later turn switching to a source-owned model is rejected

- **GIVEN** a WebSocket Responses session already has an open subscription-account upstream
- **AND** an enabled OpenAI-compatible model source exposes model `m` with Responses support
- **WHEN** a subsequent `response.create` requests model `m`
- **THEN** the system emits a terminal error with code `model_source_requires_http_transport`
- **AND** the frame is not forwarded to the subscription account on the open upstream
- **AND** the turn's usage reservation is released

#### Scenario: An alias-named source model is rejected despite normalization

- **GIVEN** an enabled OpenAI-compatible model source exposes model `gpt-5-high` with Responses support
- **AND** an API key whose `allowed_models` contains exactly `gpt-5-high`
- **WHEN** the key sends a WebSocket `response.create` for `gpt-5-high`, which enforcement normalizes to `gpt-5`
- **THEN** the source-ownership check also considers the raw `gpt-5-high` candidate
- **AND** the request is rejected with `model_source_requires_http_transport` on the connect path and on socket reuse alike

#### Scenario: A file-referencing turn is dispatched to its pinned account, not failed

- **GIVEN** a WebSocket Responses session already has an open subscription-account upstream
- **AND** a later `response.create` references an uploaded `input_file` pinned to that account
- **AND** the request's model is also exposed by an enabled model source
- **WHEN** the turn is prepared for the open socket
- **THEN** the reuse guard does not fail the turn with `model_source_requires_http_transport`
- **AND** the turn is forwarded to the pinned subscription account

#### Scenario: A terminal compaction trigger is not failed by the WebSocket guards

- **GIVEN** a `response.create` whose final top-level input item is a `compaction_trigger`
- **AND** the request's model is also exposed by an enabled model source
- **WHEN** the request reaches the connect path or an already-open subscription upstream
- **THEN** neither WebSocket guard fails the request with `model_source_requires_http_transport`
- **AND** the connect path proceeds to subscription account selection, and an open upstream receives the turn

#### Scenario: An API key that enforces a source-owned model is rejected

- **GIVEN** an API key whose `enforced_model` resolves to an enabled model source
- **WHEN** the key opens a WebSocket Responses session requesting any model
- **THEN** the enforced model is resolved against the model sources
- **AND** the session fails with `model_source_requires_http_transport`

#### Scenario: Subscription models are unaffected

- **GIVEN** a model that is not served by any enabled model source
- **WHEN** a client opens a WebSocket Responses session requesting that model
- **THEN** account selection proceeds unchanged

#### Scenario: Source resolution failure falls back to subscription selection

- **GIVEN** the model-source catalog cannot be read
- **WHEN** a client opens a WebSocket Responses session
- **THEN** account selection proceeds as it did before the guard existed
- **AND** the session is not terminated by the resolution failure
