## ADDED Requirements

### Requirement: Source models support explicit upstream aliases

The system SHALL accept an optional `upstream_model` in each source model's `raw_metadata_json`. If present, it MUST be a string containing 1 to 255 characters after trimming. The stored `model` MUST remain the client-visible identity. Absent mapping MUST preserve identity forwarding. Mapping MUST be applied exactly once, after source selection, on supported source Responses, Chat Completions, Embeddings and Audio Transcriptions HTTP requests, including existing equivalent and trailing-slash routes. Mapping MUST NOT alter authorization, model enablement, source assignment, pricing, request-log model, reservation settlement or continuity ownership.

#### Scenario: Client alias maps to an opaque upstream ID

- **GIVEN** public model `cd/gpt-6-astra` with `upstream_model` equal to `cd/linxaq`
- **WHEN** an authorized client requests `cd/gpt-6-astra`
- **THEN** the selected endpoint receives `model` equal to `cd/linxaq`
- **AND** accounting and continuity use `cd/gpt-6-astra`

#### Scenario: Identity and one-hop mapping

- **WHEN** a model has no upstream mapping
- **THEN** its upstream request retains its public model ID
- **AND** an explicitly mapped target is not recursively resolved through other model rows

#### Scenario: Existing route spelling remains authoritative

- **WHEN** a client posts an alias to `/v1/responses/` or `/backend-api/codex/responses/`
- **THEN** it is mapped as on the equivalent route without the trailing slash
- **AND** unsupported trailing-slash Chat Completions, Embeddings and Audio Transcriptions URLs retain their existing 405 error envelope without dispatch

#### Scenario: Invalid mapping is rejected

- **WHEN** a create or update supplies a null, non-string, blank or overlength `upstream_model`
- **THEN** the dashboard API rejects the payload without persisting it

#### Scenario: Alias cannot bypass source or model restrictions

- **WHEN** a key requests an alias outside its allowed models or assigned sources
- **THEN** no request is dispatched to that alias's upstream endpoint

### Requirement: Aliased source responses retain the public model identity

For a mapped source request, the proxy MUST replace an upstream model value matching the configured target in a successful response's top-level `model` or Responses event's `response.model` with the public alias. This MUST apply to JSON and SSE responses. Text, tool arguments, usage, response IDs and other fields MUST remain unchanged. Streams MUST retain cancellation cleanup and bounded buffering. Sources without an alias MUST retain existing response behavior.

#### Scenario: Streamed model fields use the alias

- **WHEN** a mapped source emits a fragmented SSE event with its upstream model ID
- **THEN** the client receives the corresponding model field as the public alias
- **AND** tool arguments and generated text containing the upstream ID remain unchanged

### Requirement: Dashboard model entry supports aliases

The create and edit source forms SHALL accept `alias=upstream-id` entries alongside bare model IDs. Forms MUST reject missing sides, multiple equals separators and duplicate public aliases. Editing a bare existing model into an alias referencing that model MUST preserve its capabilities, pricing, enablement and raw metadata. Unrelated edits MUST preserve existing mappings. Removing the mapping MUST restore identity behavior.

#### Scenario: Rename an existing model for clients

- **WHEN** an operator edits existing `cd/linxaq` into `cd/gpt-6-astra=cd/linxaq`
- **THEN** the saved public model is `cd/gpt-6-astra` with target `cd/linxaq`
- **AND** its existing multi-agent metadata and model settings are retained
