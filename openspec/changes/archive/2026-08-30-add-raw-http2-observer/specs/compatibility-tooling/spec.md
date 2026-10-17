## ADDED Requirements

### Requirement: Controlled origin captures privacy-safe raw HTTP/2 profiles

The traffic toolkit MUST provide an explicitly launched TLS HTTP/2 controlled
origin that records the client connection preface, ordered initial SETTINGS,
bounded connection-control and request frame metadata, stream identifiers,
connection reuse, decoded header-name order/casing, and header-block fragment
lengths/digests. It MUST NOT retain decoded header values, HPACK bytes, DATA
bytes, request bodies, socket peer addresses, TLS key material, or certificate
private keys. The origin MUST bind to loopback by default and MUST require an
explicit acknowledgement for a non-loopback bind.

#### Scenario: Multiple requests reuse one observed connection

- **GIVEN** a client negotiates `h2` and sends multiple controlled requests on
  one TLS connection
- **WHEN** the observer writes request records
- **THEN** the records share a connection identifier and use distinct stream
  identifiers
- **AND** no header value or request body is retained

#### Scenario: Non-h2 or oversized input is rejected safely

- **GIVEN** a client does not negotiate `h2` or exceeds a configured bound
- **WHEN** it connects to the controlled observer
- **THEN** the connection or stream is closed without an unbounded allocation
- **AND** rejected payload bytes are not persisted

### Requirement: HTTP/2 profile comparison separates gates from observations

The toolkit MUST compare Path A and Path C ordered initial SETTINGS,
pre-request connection-control shape, decoded header-name sequence, stream-id
pattern, and connection reuse independently. It MUST accept optional Path A′
evidence for direct-client variance. Missing evidence MUST be unobserved rather
than pass. HPACK fragment digests and sizes MUST remain informational and MUST
NOT be treated as proof of decoded-value or dynamic-table equality.

#### Scenario: Stable HTTP/2 profiles match

- **GIVEN** A and C contain complete controlled records with equal SETTINGS,
  connection-control shape, header-name sequence, and reuse pattern
- **WHEN** the HTTP/2 profile report is generated
- **THEN** each observed stable dimension matches
- **AND** HPACK fragment evidence remains informational

#### Scenario: SETTINGS differ

- **GIVEN** A and C advertise different ordered initial SETTINGS
- **WHEN** the HTTP/2 profile report is generated
- **THEN** SETTINGS parity fails independently of header-name parity
