## ADDED Requirements

### Requirement: Native HTTP response compression remains representation-consistent

When native HTTP egress advertises gzip response support, the helper MUST decode a gzip-encoded upstream response before relaying its body to the Python adapter. Headers relayed with the decoded body MUST describe the decoded representation and MUST NOT retain `Content-Encoding: gzip` or the encoded entity's `Content-Length`. This behavior MUST apply without changing direct or account-routed request ownership, replay policy, or streaming delivery.

#### Scenario: Native helper relays a gzip JSON response

- **GIVEN** a direct or account-routed native HTTP request advertises `Accept-Encoding: gzip`
- **WHEN** the upstream responds with a gzip-encoded JSON or SSE body and encoded-entity headers
- **THEN** the helper relays the decoded representation bytes
- **AND** the relayed headers omit the stale gzip content encoding and encoded content length
- **AND** the existing JSON or SSE adapter can consume the original representation

#### Scenario: Inbound request advertises unsupported response codings

- **GIVEN** a direct or account-routed native HTTP request includes an inbound
  `Accept-Encoding` value with codings the helper cannot decode
- **WHEN** the helper constructs the upstream request
- **THEN** it MUST remove the inbound compression negotiation
- **AND** it MUST advertise only response codings enabled in its own HTTP client
- **AND** every coding advertised by the helper MUST be decoded before relay
