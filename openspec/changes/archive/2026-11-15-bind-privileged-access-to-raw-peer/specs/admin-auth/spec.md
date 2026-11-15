## ADDED Requirements

### Requirement: Trusted-header authentication requires captured transport provenance

In trusted-header dashboard mode, identity-header sanitization and principal authentication MUST check the launcher-captured raw socket peer against the configured trusted-proxy CIDRs, with proxy-header trust enabled. They MUST NOT authorize from the forwarded client projection. An untrusted or unavailable raw peer MUST have the configured identity field removed and MUST NOT establish a trusted-header principal. Forwarded client address and scheme attribution, identity cardinality, group parsing and password fallback MUST retain their existing behavior.

#### Scenario: An untrusted peer projects a trusted client address

- **GIVEN** projection trust accepts forwarded headers from a raw peer outside the dashboard trusted-proxy CIDRs
- **WHEN** that peer projects a trusted address and supplies the configured identity field
- **THEN** the field is removed and no trusted-header actor or principal is authenticated
- **AND** protected dashboard access returns the existing authentication error while projected attribution remains available

#### Scenario: A trusted proxy projects a public client

- **GIVEN** a captured IPv4 or IPv6 raw peer belongs to a configured trusted-proxy CIDR
- **AND** proxy-header trust is enabled
- **WHEN** that proxy projects a public client and supplies one valid identity field
- **THEN** trusted-header authentication succeeds with the existing actor and group behavior
- **AND** the projected client address and scheme remain available for attribution

#### Scenario: Transport provenance is unavailable

- **WHEN** a request has no captured raw peer or its captured peer is unavailable
- **THEN** the configured identity field is removed and cannot authenticate a trusted-header principal even if the projected client is trusted
