## ADDED Requirements

### Requirement: Native HTTP response compression remains representation-consistent

Native HTTP egress MUST preserve the caller's compression-negotiation presence and value when constructing the upstream request. When a supported response coding is negotiated, the helper MUST decode the upstream response before relaying its body to the Python adapter. Headers relayed with the decoded body MUST describe the decoded representation and MUST NOT retain the stale upstream `Content-Encoding` or the encoded entity's `Content-Length`. This behavior MUST apply without changing direct or account-routed request ownership, replay policy, or streaming delivery.

#### Scenario: Native helper relays a gzip JSON response

- **GIVEN** a direct or account-routed native HTTP request advertises `Accept-Encoding: gzip`
- **WHEN** the upstream responds with a gzip-encoded JSON or SSE body and encoded-entity headers
- **THEN** the helper relays the decoded representation bytes
- **AND** the relayed headers omit the stale gzip content encoding and encoded content length
- **AND** the existing JSON or SSE adapter can consume the original representation

#### Scenario: Inbound request omits compression negotiation

- **GIVEN** a direct or account-routed native HTTP request has no `Accept-Encoding` header
- **WHEN** the helper constructs the upstream request
- **THEN** the upstream request MUST also omit `Accept-Encoding`
- **AND** the helper MUST NOT synthesize a response-coding advertisement

#### Scenario: Inbound request includes compression negotiation

- **GIVEN** a direct or account-routed native HTTP request includes an `Accept-Encoding` value using gzip, deflate, Brotli, or zstd
- **WHEN** the helper constructs and executes the upstream request
- **THEN** it MUST forward the inbound `Accept-Encoding` value unchanged
- **AND** the native HTTP client MUST have decoders enabled for gzip, deflate, Brotli, and zstd
- **AND** a response using any enabled coding MUST be decoded before relay to Python
