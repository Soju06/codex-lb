## ADDED Requirements

### Requirement: Trusted-proxy locality requires trusted socket provenance

When proxy-header trust is enabled, the system MUST classify a forwarded loopback client as local only when the raw socket peer belongs to a configured trusted-proxy CIDR and forwarded client resolution succeeds. The mere presence of a forwarded client-IP header from an untrusted socket peer MUST NOT establish locality or bypass remote dashboard bootstrap requirements.

#### Scenario: Untrusted loopback proxy cannot bypass remote bootstrap

- **WHEN** proxy-header trust is enabled
- **AND** the raw loopback socket peer is outside every configured trusted-proxy CIDR
- **AND** the request supplies a local Host header and a forwarded client-IP header
- **THEN** the request is classified as remote
- **AND** first-run password setup requires the configured bootstrap token

#### Scenario: Trusted proxy may forward a loopback client

- **WHEN** proxy-header trust is enabled
- **AND** the raw socket peer belongs to a configured trusted-proxy CIDR
- **AND** valid forwarded client resolution yields a loopback address
- **AND** the Host header is local
- **THEN** the request is classified as local

### Requirement: Direct locality inspects every forwarded client hint field

When proxy-header trust is disabled, the system MUST classify a loopback socket peer with a local Host as local only when no non-empty forwarded client-IP field value is present. When such a header occurs more than once, the system MUST inspect every field value rather than only the first.

#### Scenario: Later duplicate forwarded hint prevents local bootstrap

- **WHEN** proxy-header trust is disabled
- **AND** a loopback request with a local Host contains an empty `X-Forwarded-For` field followed by a non-empty `X-Forwarded-For` field
- **THEN** the request is classified as remote
- **AND** first-run password setup requires the configured bootstrap token
