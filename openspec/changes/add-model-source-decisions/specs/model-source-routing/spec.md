## ADDED Requirements

### Requirement: Model sources declare decision capabilities

Each model source MUST carry persisted `supports_decisions` (OpenAI
Decisions API) and `supports_systemone` (vLLM structured decisions) boolean
flags. Both MUST default to disabled. The model-source create, read, and
update contracts MUST expose both flags, and stored values MUST survive a
round trip through those contracts.

#### Scenario: existing sources default to disabled

- **GIVEN** a model source row that predates the decision capabilities
- **WHEN** the schema migration runs
- **THEN** the source reports `supports_decisions` and `supports_systemone`
  as disabled
- **AND** its existing chat-completions, responses, audio-transcription, and
  embeddings routing is unchanged

#### Scenario: capabilities round-trip through the API

- **WHEN** a client creates or updates a model source with both decision
  capabilities enabled
- **THEN** reading the source back reports both capabilities as enabled

#### Scenario: omitted capabilities parse as disabled

- **WHEN** a model-source payload omits `supports_decisions` and
  `supports_systemone`
- **THEN** it parses with both capabilities disabled rather than failing
  validation

### Requirement: Decision capabilities are independent

Enabling one decision capability MUST NOT make a source eligible for the
other decision route.

#### Scenario: OpenAI decisions do not imply the vLLM extension

- **GIVEN** a source that declares only `supports_decisions` for a model
- **WHEN** a client posts that model to `/v1/systemone`
- **THEN** the proxy returns `model_not_found`

#### Scenario: the vLLM extension does not imply OpenAI decisions

- **GIVEN** a source that declares only `supports_systemone` for a model
- **WHEN** a client posts that model to `/v1/decisions`
- **THEN** the proxy returns `model_not_found`

### Requirement: Decision requests route only to capable model sources

The system SHALL expose `POST /v1/decisions` and `POST /v1/systemone`, each
served only from an enabled `openai_compatible` model source that declares the
matching capability and has the requested model enabled. Decision requests
MUST NOT fall back to subscription-backed accounts. For an API key restricted
to a set of sources, selection MUST stay inside that set.

#### Scenario: capable source serves the request

- **GIVEN** an enabled model source declaring the matching decision
  capability with the requested model enabled
- **WHEN** a client posts to `/v1/decisions` or `/v1/systemone`
- **THEN** the proxy forwards the payload to the source's `/decisions` or
  `/systemone` endpoint respectively and returns the upstream JSON response

#### Scenario: no capable source is a model error

- **GIVEN** no enabled model source declares the matching decision capability
  for the requested model
- **WHEN** a client posts to the decision route
- **THEN** the proxy returns 404 with an OpenAI-format error envelope using
  code `model_not_found`
- **AND** the request is not routed to a subscription-backed account

### Requirement: Decision requests are validated before dispatch

`/v1/decisions` MUST require `model`, `input`, and `questions`.
`/v1/systemone` MUST require `model`, `state`, and `questions`; `model` is
required even though vLLM treats it as optional, because the proxy routes on
it. Invalid requests MUST be rejected with an OpenAI-format
`invalid_request_error` envelope before any source is contacted.

#### Scenario: structured decision without a model is rejected

- **WHEN** a client posts to `/v1/systemone` without a `model` field
- **THEN** the proxy returns 400 with an `invalid_request_error` envelope
- **AND** no model source is contacted

### Requirement: Decision usage settles API key reservations

Decision responses MUST be read for Responses-style token usage
(`input_tokens`, `input_tokens_details.cached_tokens`, `output_tokens`,
`total_tokens`). When the caller's API key requires usage for settlement and
the source response reports none, the proxy MUST fail closed with
`usage_unavailable` rather than serve unmetered traffic.

#### Scenario: reported usage settles the reservation

- **GIVEN** an API key with a token limit
- **WHEN** the model source returns a decision response reporting usage
- **THEN** the key's limit advances by the reported total tokens
- **AND** the request log records the input, cached input, and output tokens

#### Scenario: missing usage fails closed for a limited key

- **GIVEN** an API key whose reservation requires reported usage
- **WHEN** the model source returns a decision response without a usage
  object
- **THEN** the proxy returns an error envelope using code `usage_unavailable`

### Requirement: Dispatched decision attempts are request-logged

Every decision attempt dispatched to a model source MUST produce a
request-log entry: `success` on a forwarded response, `error` on a forwarding,
usage, or settlement failure. The entry MUST carry the upstream status code
when the source returned an HTTP response and MUST leave it absent when no
response was received. A request rejected before source selection MUST NOT
produce an entry.

#### Scenario: forwarding error propagates the upstream status

- **WHEN** the model source returns an error status for a decision request
- **THEN** the proxy returns an error with that status
- **AND** records an error request log carrying the upstream status code

#### Scenario: transport failure records an attempt without an upstream status

- **WHEN** the request to the model source fails before any HTTP response is
  received
- **THEN** the proxy returns `model_source_unreachable`
- **AND** records an error request log with no upstream status code

### Requirement: Decision source forwarding preserves field presence

For source-routed decision requests, the system MUST preserve both the values
and presence of fields beyond the validated fields. A field explicitly
supplied as null MUST be forwarded as null, a field omitted by the client MUST
remain absent, and a non-null field MUST be forwarded unchanged.

#### Scenario: explicit null extras remain present

- **WHEN** a client supplies an optional field as null in a source-routed
  decision request
- **THEN** the compatible source receives the key with a null value

#### Scenario: omitted extras remain absent

- **WHEN** a client omits an optional field from a source-routed decision
  request
- **THEN** the compatible source payload does not contain that key

### Requirement: Source-only routes reject metadata-only capability intent

`POST /v1/embeddings`, `POST /v1/decisions`, and `POST /v1/systemone` MUST
reject a required capability carried only in the request body's
`client_metadata` with the same transport-unsupported error returned for the
required-capability header, before source selection, so the signal is never
forwarded to a model source.

#### Scenario: metadata-only capability signal is denied

- **GIVEN** a request to a source-only route carrying a required capability
  only in `client_metadata`
- **WHEN** the request is admitted
- **THEN** the proxy returns 400 with code
  `required_capability_transport_unsupported`
- **AND** no model source is selected or contacted
