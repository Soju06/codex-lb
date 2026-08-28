## ADDED Requirements

### Requirement: Multipart ingress exceptions are exact and route-owned

The generic raw HTTP body guard MUST exempt an unencoded `multipart/form-data` request only when the request is `POST` to `/api/accounts/import`, `/backend-api/transcribe`, `/v1/audio/transcriptions`, or `/v1/images/edits`, including their application-relative trailing-slash and mounted equivalents. Each exempt operation MUST apply its capability-defined authorization-before-read contract and dedicated bounded multipart parser.

For the same exact operations, an outer content-encoding gate MUST remove `Content-Encoding` and mark the copied request scope as route-owned without reading the body. The generic raw and decompression guards MUST honor that internal marker regardless of the declared media type so `identity` reaches dedicated multipart admission and non-identity encoding reaches the post-authorization rejection path.

The client-declared multipart media type MUST NOT exempt any other method or path. Every unrelated unencoded or encoded multipart request MUST remain under generic raw admission, and encoded requests MUST also retain generic decompressed-body admission.

#### Scenario: Exact unencoded multipart operation uses its dedicated parser

- **WHEN** an unencoded multipart request targets one of the four exact route-owned `POST` operations
- **THEN** generic admission does not preempt operation authorization
- **AND** the operation's dedicated multipart body limit remains authoritative

#### Scenario: Exact encoded operation preserves authorization precedence

- **WHEN** a request with `Content-Encoding` targets one of the four exact route-owned `POST` operations and declares either multipart or another media type
- **THEN** the outer gate marks the request without consuming its body
- **AND** generic raw or decompressed-body admission does not preempt operation authorization or its encoded-body contract

#### Scenario: Unrelated multipart media type grants no exemption

- **WHEN** an unencoded or encoded request outside the four exact route-owned `POST` operations declares `multipart/form-data`
- **THEN** the generic raw-body budget remains enforced
- **AND** an oversized declared body is rejected before downstream parsing
