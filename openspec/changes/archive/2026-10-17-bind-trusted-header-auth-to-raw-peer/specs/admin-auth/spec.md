## ADDED Requirements

### Requirement: Trusted-header authentication requires trusted socket provenance

When dashboard trusted-header authentication is enabled, the system MUST
authorize the configured identity header against the launcher-preserved raw
socket peer. The raw peer MUST belong to a configured trusted-proxy CIDR and
proxy-header trust MUST be enabled before the header can establish a dashboard
principal. A projected client address MUST NOT replace this provenance check.
If raw-peer capture is missing or the raw peer is untrusted, the identity
header MUST be ignored and trusted-header authentication MUST NOT establish a
dashboard principal. Before downstream HTTP handling, the identity-header
sanitizer MUST remove the configured identity header whenever raw-peer capture
is missing, the raw peer is untrusted, or proxy-header trust is disabled.

#### Scenario: Trusted proxy supplies dashboard identity

- **WHEN** the raw socket peer belongs to a configured trusted-proxy CIDR
- **AND** proxy-header trust and trusted-header authentication are enabled
- **AND** the proxy supplies the configured identity header
- **THEN** the request is authenticated with that trusted-header principal
- **AND** forwarded client projection remains available to downstream handling

#### Scenario: Untrusted peer cannot project itself into the trusted network

- **WHEN** the raw socket peer is outside every configured trusted-proxy CIDR
- **AND** the request supplies a forwarded client address inside a trusted-proxy CIDR
- **AND** the request supplies the configured identity header
- **THEN** the configured identity header is removed before downstream HTTP handling
- **AND** trusted-header authentication does not establish a dashboard principal

#### Scenario: Missing raw-peer capture fails closed

- **WHEN** trusted-header authentication receives a request without preserved
  raw socket provenance
- **THEN** the configured identity header is removed before downstream HTTP handling
- **AND** trusted-header authentication does not establish a dashboard principal
