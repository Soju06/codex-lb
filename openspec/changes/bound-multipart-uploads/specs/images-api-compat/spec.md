## ADDED Requirements

### Requirement: Image edit multipart uploads are authorized and bounded

`POST /v1/images/edits` MUST complete its existing proxy authorization dependencies before reading multipart body bytes. It MUST accept at most 16 source-image file parts across `image` and `image[]`, at most one `mask`, no unknown file-part names, no more than 32 text fields of at most 256 KiB each, every individual file smaller than 50,000,000 bytes, fewer than 50,000,000 bytes across all source images and the mask, and a complete multipart body no greater than 64 MiB (67,108,864 bytes).

The service MUST enforce the body limit against both a usable declared `Content-Length` and actual streamed bytes. It MUST enforce file, aggregate-binary, and text limits before retaining crossing bytes, close multipart spools before usage reservation, account selection, base64 conversion, or internal Responses forwarding, and add no new runtime setting.

This route-owned policy MUST take precedence over the generic raw HTTP body budget for `POST /v1/images/edits`. Its exact-path content-encoding gate MUST run outside the generic raw and decompression guards regardless of the declared media type. Requests handled by that gate, and unencoded requests declared as multipart, MUST NOT be rejected by the generic guards before proxy authorization or the dedicated parser applies this capability's body limit. An unencoded request that does not declare multipart remains under generic admission and MAY be rejected there before authorization. This exception MUST NOT change generic ingress behavior for any other operation.

Byte-limit failures MUST return HTTP 413 with OpenAI error `code = payload_too_large` and `type = invalid_request_error`; a known file-part failure MUST set `param = image` or `param = mask`. Multipart syntax, count, and required-field failures MUST retain OpenAI-compatible invalid-request behavior. Every parser rejection MUST emit exactly one bounded image-route observation with HTTP status and `outcome = invalid_request`.

#### Scenario: Unauthorized image edit does not consume the body

- **WHEN** an image-edit request fails the existing proxy API-key authorization
- **THEN** the authentication response is returned before the ASGI request body is consumed
- **AND** no multipart temporary file is created
- **AND** exactly one auth-error route observation is recorded without parsing the multipart body, using bounded pre-parse labels

#### Scenario: Bounded image edit remains compatible

- **WHEN** an authorized image-edit request supplies at least one source image, an optional mask, required text fields, and all parts are within their limits
- **THEN** the service preserves the ordered `image` and `image[]` bytes, content types, mask, and validated form fields through the existing image-edit pipeline

#### Scenario: Source image count combines canonical and bracketed keys

- **WHEN** the combined number of `image` and `image[]` file parts exceeds 16, the request contains more than one `mask`, or an unknown file-part name is present
- **THEN** the service returns an OpenAI-compatible HTTP 400 invalid-request response
- **AND** no image bytes are base64-encoded or forwarded internally

#### Scenario: Declared or streamed image-edit body exceeds its limit

- **WHEN** a usable `Content-Length` exceeds 64 MiB or actual streamed multipart bytes cross 64 MiB
- **THEN** the service returns HTTP 413 with OpenAI error `code = payload_too_large` and `type = invalid_request_error`
- **AND** no usage reservation, account selection, base64 conversion, or internal Responses request occurs

#### Scenario: Image binary limit is exceeded

- **WHEN** one source image or mask reaches 50,000,000 bytes, or their combined binary bytes reach 50,000,000
- **THEN** the service returns HTTP 413 with OpenAI error `code = payload_too_large`, `type = invalid_request_error`, and the applicable `image` or `mask` parameter
- **AND** bytes beyond the applicable limit are not retained in a spool or handler buffer

#### Scenario: Image text-field resources are bounded

- **WHEN** an image-edit request exceeds 32 text fields or 256 KiB in any text part
- **THEN** the service rejects the request with the documented OpenAI-compatible count or byte-limit response
- **AND** it records one invalid-request route observation without invoking image-edit route logic

#### Scenario: Compressed image edit is rejected without prebuffering

- **GIVEN** image edit has passed proxy authorization
- **WHEN** it declares a non-identity `Content-Encoding`
- **THEN** the service returns HTTP 400 with OpenAI error `code = invalid_request_error` and `type = invalid_request_error` before reading the request body
- **AND** a no-op `identity` encoding is handled as an ordinary multipart request governed by the 64 MiB dedicated body limit
- **AND** exactly one invalid-request route observation is recorded without parsing the multipart body

#### Scenario: Generic ingress does not preempt encoded image-edit authorization

- **GIVEN** an image-edit request fails proxy authorization
- **WHEN** it declares a non-identity `Content-Encoding` and a `Content-Length` greater than the generic raw HTTP budget
- **THEN** the existing authentication response is returned instead of a generic HTTP 413 or encoded-body HTTP 400
- **AND** the request body is not consumed
- **AND** exactly one auth-error route observation is recorded

#### Scenario: Image-edit cleanup preserves transport failures

- **WHEN** parsing succeeds, fails a limit, encounters malformed multipart, receives a client disconnect, or is cancelled
- **THEN** every created multipart spool is closed
- **AND** disconnect and cancellation are not converted to HTTP 413
