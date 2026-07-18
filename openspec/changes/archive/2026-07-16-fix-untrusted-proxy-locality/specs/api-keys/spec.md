## ADDED Requirements

### Requirement: Untrusted forwarded headers do not grant unauthenticated proxy locality

When API-key authentication is disabled and proxy-header trust is enabled, forwarded client-IP headers from a socket peer outside every configured trusted-proxy CIDR MUST NOT cause a protected proxy request to be classified as local. Such a request MUST remain blocked unless its raw socket peer independently matches `proxy_unauthenticated_client_cidrs`.

#### Scenario: Untrusted loopback proxy remains blocked

- **WHEN** API-key authentication is disabled
- **AND** proxy-header trust is enabled
- **AND** the raw loopback socket peer is outside every configured trusted-proxy CIDR
- **AND** a forwarded client-IP header is present
- **AND** the raw socket peer is outside `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy request is rejected with HTTP 401

#### Scenario: Explicit raw-socket allowlist remains authoritative

- **WHEN** the raw socket peer belongs to `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy request may proceed without API-key authentication
- **AND** forwarded header contents do not determine that allowlist match

### Requirement: Direct-local proxy access inspects every forwarded client hint field

When API-key authentication and proxy-header trust are disabled, a loopback socket peer MUST qualify for direct-local protected proxy access only when no non-empty forwarded client-IP field value is present. The system MUST inspect every repeated field value; a later non-empty value MUST keep the request blocked unless the raw socket peer independently matches `proxy_unauthenticated_client_cidrs`.

#### Scenario: Later duplicate forwarded hint remains unauthorized

- **WHEN** API-key authentication and proxy-header trust are disabled
- **AND** a loopback request contains an empty `X-Forwarded-For` field followed by a non-empty `X-Forwarded-For` field
- **AND** the raw socket peer is outside `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy request is rejected with HTTP 401
