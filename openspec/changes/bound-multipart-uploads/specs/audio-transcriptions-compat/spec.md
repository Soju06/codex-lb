## ADDED Requirements

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
