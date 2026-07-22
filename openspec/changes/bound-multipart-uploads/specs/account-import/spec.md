## ADDED Requirements

### Requirement: Account auth imports are authorized and bounded

`POST /api/accounts/import` MUST authenticate the dashboard session and require dashboard write access before reading any request-body bytes. It MUST accept exactly one file part named `auth_json`, no text parts, a file size no greater than 1 MiB (1,048,576 bytes), and a complete multipart body no greater than 2 MiB (2,097,152 bytes).

The service MUST enforce the body limit against both a usable declared `Content-Length` and the actual streamed bytes. It MUST enforce the file limit before retaining bytes beyond the limit, close every multipart spool before account persistence or import-time network work begins, and add no new runtime setting.

This route-owned policy MUST take precedence over the generic raw HTTP body budget for `POST /api/accounts/import`. Its exact-path content-encoding gate MUST run outside the generic raw and decompression guards regardless of the declared media type. Requests handled by that gate, and unencoded requests declared as multipart, MUST NOT be rejected by the generic guards before dashboard authorization or the dedicated parser applies this capability's body limit. An unencoded request that does not declare multipart remains under generic admission and MAY be rejected there before authorization. This exception MUST NOT change generic ingress behavior for any other operation.

Byte-limit failures MUST return HTTP 413 with dashboard error `code = payload_too_large`. Missing or non-file `auth_json` input MUST retain the dashboard validation envelope, while malformed multipart syntax or additional parts MUST return a dashboard-compatible HTTP 400 without invoking account import logic.

#### Scenario: Unauthorized import does not consume the body

- **WHEN** a request without a valid dashboard session or write permission targets account import
- **THEN** the existing authentication or permission response is returned before the ASGI request body is consumed
- **AND** no multipart temporary file is created

#### Scenario: Valid bounded auth file imports normally

- **WHEN** an authorized operator uploads exactly one valid `auth_json` file and both file and multipart body are within their limits
- **THEN** the existing account identity, persistence, usage-refresh, cache-invalidation, and audit behavior continues
- **AND** the multipart spool is closed before persistence or network work begins

#### Scenario: Declared or streamed account-import body exceeds its limit

- **WHEN** a usable `Content-Length` exceeds 2 MiB or actual streamed multipart bytes cross 2 MiB
- **THEN** the service returns HTTP 413 with dashboard error `code = payload_too_large`
- **AND** it does not parse credentials, mutate an account, refresh usage, invalidate caches, or write a success audit event

#### Scenario: Auth file exceeds its limit

- **WHEN** the `auth_json` file part exceeds 1 MiB while the multipart body is otherwise valid
- **THEN** the service returns HTTP 413 with dashboard error `code = payload_too_large`
- **AND** bytes beyond the file limit are not retained in a multipart spool or handler buffer

#### Scenario: Account import has an invalid multipart shape

- **WHEN** an import omits a file-valued `auth_json` part or includes duplicate, additional file, or text parts
- **THEN** the service returns the established dashboard validation or bad-request envelope
- **AND** account import logic is not invoked

#### Scenario: Compressed account import is rejected without prebuffering

- **GIVEN** account import has passed dashboard session and write authorization
- **WHEN** it declares a non-identity `Content-Encoding`
- **THEN** the service returns HTTP 400 with dashboard error `code = invalid_request` before reading the request body
- **AND** a no-op `identity` encoding is handled as an ordinary multipart request governed by the 2 MiB dedicated body limit

#### Scenario: Generic ingress does not preempt encoded account-import authorization

- **GIVEN** an account-import request fails dashboard session or write authorization
- **WHEN** it declares a non-identity `Content-Encoding` and a `Content-Length` greater than the generic raw HTTP budget
- **THEN** the existing authentication or permission response is returned instead of a generic HTTP 413 or encoded-body HTTP 400
- **AND** the request body is not consumed

#### Scenario: Disconnect and cancellation clean up parsing

- **WHEN** the client disconnects or request processing is cancelled during account multipart parsing
- **THEN** every created spool is closed
- **AND** the disconnect or cancellation propagates without being converted to HTTP 413
