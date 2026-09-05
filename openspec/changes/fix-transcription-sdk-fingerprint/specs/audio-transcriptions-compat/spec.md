## MODIFIED Requirements

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
