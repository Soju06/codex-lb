## ADDED Requirements

### Requirement: Codex control responses negotiate identity encoding

Unary Codex control requests, including standalone search, MUST send exactly
one case-insensitive `Accept-Encoding` field with value `identity` to upstream
on both native and Python HTTP transports. An inbound encoding field MUST be
replaced at its first existing spelling and position, with duplicate case
variants removed. The field MUST be added once when absent. The proxy MUST
preserve opaque request bytes, successful response bytes, and the existing
control response-header allowlist and error normalization contract.

#### Scenario: compressed client preferences still yield decodable search JSON

- **GIVEN** upstream returns gzip when requested and identity when negotiated
- **WHEN** a native Codex client requests a supported search path with compression enabled
- **THEN** upstream receives `Accept-Encoding: identity`
- **AND** the downstream 200 body decodes as the unchanged search JSON
- **AND** a client receives the search output and results

#### Scenario: error envelopes remain decodable

- **WHEN** upstream rejects an identity-negotiated control request with a JSON error
- **THEN** the proxy preserves the upstream error status and normalizes the JSON error envelope

#### Scenario: encoding header casing does not create duplicates

- **WHEN** inbound encoding preferences are absent or use one or more case variants
- **THEN** exactly one identity-encoding field is sent on either HTTP transport
- **AND** an existing field retains its first spelling and position
