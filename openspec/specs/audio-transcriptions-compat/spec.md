# audio-transcriptions-compat Specification

## Purpose
TBD - created by archiving change add-transcription-proxy-compat. Update Purpose after archive.
## Requirements
### Requirement: Native transcription proxy endpoint
The system SHALL expose `POST /backend-api/transcribe` for multipart audio transcription requests. The endpoint MUST accept a multipart `file` part and MAY accept a `prompt` part, and MUST forward requests to upstream `/transcribe` using selected account credentials. While forwarding multipart form data, the service MUST strip inbound `Content-Type` header values case-insensitively so the upstream client can generate a correct boundary. For a non-native Codex client, the upstream request MUST use canonical `codex_cli_rs` `User-Agent`, `originator`, and `version` values and MUST NOT forward OpenAI SDK fingerprint headers including `x-stainless-*`. A native Codex client MUST forward its inbound `User-Agent` unchanged and MUST NOT add canonical `originator` or `version` headers.

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

#### Scenario: OpenAI SDK transcription fingerprint is normalized
- **WHEN** a non-native client sends `/v1/audio/transcriptions` with an `OpenAI/JS` user agent and `x-stainless-*` headers
- **THEN** upstream `/transcribe` receives canonical `codex_cli_rs` `User-Agent`, `originator`, and `version` values
- **AND** upstream does not receive the OpenAI SDK user agent or any `x-stainless-*` header
- **AND** upstream receives selected-account authorization and ChatGPT account id, generated multipart data, and the transcription response remains OpenAI-compatible

#### Scenario: Native Codex transcription fingerprint is preserved
- **WHEN** a native Codex client sends a transcription request
- **THEN** upstream receives the inbound `User-Agent` unchanged
- **AND** upstream does not receive canonical `originator` or `version` headers

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

### Requirement: Transcription proxy requests use a bounded retry budget
The system MUST enforce a configurable total request budget for transcription proxy routes across account selection, token refresh, upstream connect, and upstream response handling. Once that budget is exhausted, the proxy MUST stop retrying and return a stable OpenAI-format timeout failure instead of waiting through repeated hard-coded timeout windows.

#### Scenario: Transcription budget expires before retry
- **WHEN** a transcription request consumes its configured request budget before a retry attempt can begin
- **THEN** the service returns `502` with OpenAI-format error code `upstream_unavailable`
- **AND** no further upstream attempt starts

#### Scenario: 401 transcription retry respects remaining budget
- **WHEN** the first transcription attempt returns 401 and token refresh succeeds while request budget remains
- **THEN** the retry uses the refreshed account metadata
- **AND** the retry only proceeds if enough request budget remains for another attempt

### Requirement: Transcription multipart uploads are authorized and bounded

`POST /backend-api/transcribe` and `POST /v1/audio/transcriptions` MUST complete their existing proxy authorization dependencies before reading multipart body bytes. Each request MUST contain exactly one file part no greater than 25,000,000 bytes, no more than 32 text fields of at most 256 KiB each, and a complete multipart body no greater than 32 MiB (33,554,432 bytes).

The service MUST enforce the body limit against both a usable declared `Content-Length` and actual streamed bytes. It MUST enforce file and text limits before retaining crossing bytes, close multipart spools before usage reservation, account selection, or upstream forwarding, preserve ordered text-field forwarding for configured model sources, and add no new runtime setting.

This route-owned policy MUST take precedence over the generic raw HTTP body budget for both transcription operations. Their exact-path content-encoding gate MUST run outside the generic raw and decompression guards regardless of the declared media type. Requests handled by that gate, and unencoded requests declared as multipart, MUST NOT be rejected by the generic guards before proxy authorization or the dedicated parser applies this capability's body limit. An unencoded request that does not declare multipart remains under generic admission and MAY be rejected there before authorization. This exception MUST NOT change generic ingress behavior for any other operation.

Byte-limit failures MUST return HTTP 413 with OpenAI error `code = payload_too_large` and `type = invalid_request_error`; a file-part failure MUST set `param = file`. Multipart syntax, count, and required-field failures MUST retain OpenAI-compatible invalid-request behavior and MUST NOT reserve usage or call upstream.

#### Scenario: Unauthorized transcription does not consume the body

- **WHEN** a transcription request fails the existing proxy API-key authorization
- **THEN** the authentication response is returned before the ASGI request body is consumed
- **AND** no multipart temporary file is created

#### Scenario: Bounded native transcription remains compatible

- **WHEN** an authorized `/backend-api/transcribe` request supplies one audio file within 25,000,000 bytes, an optional bounded prompt, and a multipart body within 32 MiB
- **THEN** the service forwards the same audio bytes, filename, content type, and prompt through the existing transcription pipeline

#### Scenario: Bounded source-model transcription preserves fields

- **WHEN** an authorized `/v1/audio/transcriptions` request selects a configured model source and all multipart limits are satisfied
- **THEN** the service forwards the audio file and the ordered non-file form fields through the existing source pipeline

#### Scenario: Declared or streamed transcription body exceeds its limit

- **WHEN** a usable `Content-Length` exceeds 32 MiB or actual streamed multipart bytes cross 32 MiB
- **THEN** the service returns HTTP 413 with OpenAI error `code = payload_too_large` and `type = invalid_request_error`
- **AND** no usage reservation, account selection, or upstream request occurs

#### Scenario: Transcription file exceeds its limit

- **WHEN** the audio file part exceeds 25,000,000 bytes
- **THEN** the service returns HTTP 413 with OpenAI error `code = payload_too_large`, `type = invalid_request_error`, and `param = file`
- **AND** bytes beyond the file limit are not retained in a spool or handler buffer

#### Scenario: Transcription field resources are bounded

- **WHEN** a request exceeds 32 text fields, one file part, or 256 KiB in any text part
- **THEN** the service rejects the request with the documented OpenAI-compatible count or byte-limit response
- **AND** it does not invoke transcription route logic

#### Scenario: Compressed transcription is rejected without prebuffering

- **GIVEN** the transcription request has passed proxy authorization
- **WHEN** either transcription route declares a non-identity `Content-Encoding`
- **THEN** the service returns HTTP 400 with OpenAI error `code = invalid_request_error` and `type = invalid_request_error` before reading the request body
- **AND** a no-op `identity` encoding is handled as an ordinary multipart request governed by the 32 MiB dedicated body limit

#### Scenario: Generic ingress does not preempt encoded transcription authorization

- **GIVEN** a request to either transcription route fails proxy authorization
- **WHEN** it declares a non-identity `Content-Encoding` and a `Content-Length` greater than the generic raw HTTP budget
- **THEN** the existing authentication response is returned instead of a generic HTTP 413 or encoded-body HTTP 400
- **AND** the request body is not consumed

#### Scenario: Transcription cleanup preserves transport failures

- **WHEN** parsing succeeds, fails a limit, encounters malformed multipart, receives a client disconnect, or is cancelled
- **THEN** every created multipart spool is closed
- **AND** disconnect and cancellation are not converted to HTTP 413
