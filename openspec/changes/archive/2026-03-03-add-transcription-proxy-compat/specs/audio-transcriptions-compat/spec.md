## ADDED Requirements

### Requirement: Native transcription proxy endpoint
The system SHALL expose `POST /backend-api/transcribe` for multipart audio transcription requests. The endpoint MUST accept a multipart `file` part and MAY accept a `prompt` part, and MUST forward requests to upstream `/transcribe` using selected account credentials. While forwarding multipart form data, the service MUST strip inbound `Content-Type` header values case-insensitively so the upstream client can generate a correct boundary.

#### Scenario: Native transcription request is forwarded
- **WHEN** a client sends multipart data with `file` (and optional `prompt`) to `/backend-api/transcribe`
- **THEN** the service forwards multipart data to upstream `/transcribe` and returns the upstream JSON response

#### Scenario: Upstream transcription error is propagated
- **WHEN** upstream `/transcribe` returns an error response
- **THEN** the service returns an OpenAI-format error envelope with the upstream status code

#### Scenario: Upstream transcription timeout is mapped to unavailable
- **WHEN** forwarding to upstream `/transcribe` times out before a response is received
- **THEN** the service returns 502 with an OpenAI-format error envelope using code `upstream_unavailable`

#### Scenario: Upstream transcription body-read timeout is mapped to unavailable
- **WHEN** upstream accepts a transcription request but times out or drops transport while the proxy reads the JSON response body
- **THEN** the service returns 502 with an OpenAI-format error envelope using code `upstream_unavailable` instead of `upstream_error`

#### Scenario: Multipart forwarding ignores inbound Content-Type case
- **WHEN** inbound transcription headers include `content-type` or `Content-Type`
- **THEN** the upstream multipart request is sent without forwarding that header and uses a freshly generated multipart boundary

### Requirement: OpenAI-compatible audio transcription endpoint
The system SHALL expose `POST /v1/audio/transcriptions` and enforce OpenAI-compatible request semantics for transcription. The endpoint MUST require multipart `file` and `model`, MUST accept optional `prompt`, and MUST reject requests where `model` is not exactly `gpt-4o-transcribe`.

#### Scenario: Compatible transcription request succeeds
- **WHEN** a client sends multipart `file`, `model=gpt-4o-transcribe`, and optional `prompt` to `/v1/audio/transcriptions`
- **THEN** the service forwards transcription to upstream `/transcribe` and returns upstream JSON

#### Scenario: Unsupported transcription model is rejected
- **WHEN** a client sends `/v1/audio/transcriptions` with `model` not equal to `gpt-4o-transcribe`
- **THEN** the service returns 400 with OpenAI `invalid_request_error` and `param` set to `model`

### Requirement: Transcription routes enforce proxy policy with fixed effective model
The system SHALL apply the same proxy policy checks on transcription routes as other proxy routes. For policy evaluation, both transcription routes MUST use effective model `gpt-4o-transcribe` for API key allowed-model checks and model-scoped limit applicability.

#### Scenario: API key allowed-model policy blocks transcription
- **WHEN** API key auth is enabled and the API key `allowed_models` does not include `gpt-4o-transcribe`
- **THEN** requests to `/backend-api/transcribe` or `/v1/audio/transcriptions` are rejected with 403 `model_not_allowed`

#### Scenario: Model-scoped limit applies to transcription effective model
- **WHEN** a model-scoped API key limit for `gpt-4o-transcribe` is exhausted
- **THEN** transcription requests are rejected with 429 rate limit error

### Requirement: Transcription account selection does not depend on model-registry membership
When selecting an account for transcription routes, the system MUST NOT filter candidates by model-registry plan support for `gpt-4o-transcribe`. The system SHALL still return `no_accounts` only when no active accounts are available after normal selection rules.

#### Scenario: Registry lacks transcription model but active account exists
- **WHEN** model registry does not list `gpt-4o-transcribe` and at least one active account exists
- **THEN** transcription routing still selects an active account instead of failing model-plan filtering

#### Scenario: No active accounts for transcription
- **WHEN** no active accounts are available
- **THEN** transcription request returns an OpenAI-format `no_accounts` error

### Requirement: Transcription retry uses refreshed account metadata
When an upstream transcription request returns 401 and token refresh succeeds, the retry attempt MUST rebuild upstream account metadata from the refreshed account record.

#### Scenario: Refresh updates account identifier before retry
- **WHEN** the first transcription attempt returns 401 and refresh updates `chatgpt_account_id`
- **THEN** the retry sends the updated account id header value to upstream

### Requirement: Initial transcription refresh failures are handled as proxy errors
When transcription account freshness checks fail before the first upstream call, the service MUST catch refresh failures and return a handled proxy error response instead of an unhandled internal error.

#### Scenario: Initial refresh failure returns handled error envelope
- **WHEN** selected transcription account refresh fails during the initial `_ensure_fresh` call
- **THEN** the request returns an OpenAI-format error envelope with non-500 status and does not surface an unhandled exception
