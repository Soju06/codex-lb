## MODIFIED Requirements

### Requirement: Model discovery emits a Codex control identity

Subscription model-discovery requests MUST send the resolved Codex client
version in the request query and a first-party Codex `User-Agent`, MUST send
`originator` and `Accept: */*`, and MUST apply the same mapping for direct and
account-routed egress. They MUST NOT expose the HTTP library's default
User-Agent or add a standalone `version` header absent from the maintained
direct-Codex profile.

#### Scenario: Routed and direct model discovery share identity

- **GIVEN** the same resolved Codex version, access token, and account
- **WHEN** model discovery runs once through direct egress and once through an
  account route
- **THEN** both requests carry the same ordered Codex identity and accept
  headers
- **AND** neither carries an aiohttp-generated User-Agent or standalone
  `version` header

### Requirement: HTTP/2 profile comparison separates gates from observations

The toolkit MUST compare Path A and Path C ordered initial SETTINGS,
client-initiated pre-request connection-control shape, decoded header-name
sequence, stream-id pattern, and connection reuse independently. It MUST accept
optional Path A′ evidence for direct-client variance. A SETTINGS ACK generated
in reaction to observer server settings MUST be excluded from the stable
connection-control projection because its position relative to first HEADERS is
server-timing dependent. Missing evidence MUST be unobserved rather than pass.
HPACK fragment digests and sizes MUST remain informational and MUST NOT be
treated as proof of decoded-value or dynamic-table equality.

#### Scenario: Stable HTTP/2 profiles match

- **GIVEN** A and C contain complete controlled records with equal initial
  SETTINGS, client-initiated connection-control shape, header-name sequence,
  and reuse pattern
- **WHEN** the HTTP/2 profile report is generated
- **THEN** each observed stable dimension matches
- **AND** HPACK fragment evidence remains informational

#### Scenario: SETTINGS differ

- **GIVEN** A and C advertise different ordered initial SETTINGS
- **WHEN** the HTTP/2 profile report is generated
- **THEN** SETTINGS parity fails independently of header-name parity

#### Scenario: Observer response timing moves SETTINGS ACK

- **GIVEN** A′ sends its SETTINGS ACK before first HEADERS and A sends the same
  ACK after first HEADERS
- **WHEN** their stable connection-control shapes are compared
- **THEN** the reactive ACK position does not create a direct-variance mismatch
- **AND** non-ACK SETTINGS and WINDOW_UPDATE frames remain exact evidence

## ADDED Requirements

### Requirement: Raw HTTP/2 parity gates wire-profile changes

Changes to native HTTP/2 startup settings or native Codex header serialization
MUST be verified with controlled A′, A, and C captures. A′ and A MUST establish
direct repeatability, and the ordered SETTINGS in A and C, connection-control shape,
decoded header-name order/casing, and stream/reuse pattern MUST all match before
the wire-profile change is considered verified. HPACK fragments MUST remain
informational.

#### Scenario: Native wire-profile fix is verified

- **GIVEN** focused unit tests pass and independent A′/A direct profiles match
- **WHEN** the fixed native helper is captured as Path C
- **THEN** every stable A/C HTTP/2 profile dimension matches
- **AND** credential values, request bodies, HPACK bytes, and TLS keys are not
  retained as evidence
