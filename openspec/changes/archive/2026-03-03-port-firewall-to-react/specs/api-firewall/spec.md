## ADDED Requirements

### Requirement: Firewall allowlist management API
Dashboard API MUST expose firewall allowlist management endpoints at `/api/firewall/ips` for listing, creating, and deleting allowed client IP addresses.

#### Scenario: Empty allowlist means allow-all mode
- **WHEN** no firewall entries exist
- **THEN** `GET /api/firewall/ips` returns `mode = "allow_all"` and an empty `entries` array

#### Scenario: Creating a valid IP entry
- **WHEN** dashboard client calls `POST /api/firewall/ips` with a valid IPv4 or IPv6 address
- **THEN** the service stores normalized IP value and returns the created entry with `createdAt`

#### Scenario: Duplicate IP creation
- **WHEN** dashboard client calls `POST /api/firewall/ips` with an IP already in allowlist
- **THEN** API returns conflict error with code `ip_exists`

#### Scenario: Invalid IP creation
- **WHEN** dashboard client calls `POST /api/firewall/ips` with invalid IP string
- **THEN** API returns bad-request error with code `invalid_ip`

#### Scenario: Removing unknown IP
- **WHEN** dashboard client calls `DELETE /api/firewall/ips/{ip}` for missing entry
- **THEN** API returns not-found error with code `ip_not_found`

### Requirement: Firewall enforcement for protected proxy paths
The application MUST enforce firewall allowlist for proxy-facing paths `/backend-api/codex/*` and `/v1/*`.

#### Scenario: Allowlist disabled when empty
- **WHEN** allowlist is empty
- **THEN** protected proxy requests are allowed

#### Scenario: Allowlist active blocks unlisted client
- **WHEN** allowlist contains one or more IP entries and request client IP is not listed
- **THEN** protected proxy request returns HTTP 403 with OpenAI-style error code `ip_forbidden`

#### Scenario: Dashboard endpoints are not restricted
- **WHEN** allowlist is active
- **THEN** dashboard endpoints under `/api/*` remain accessible (subject to dashboard auth only)

### Requirement: Trusted proxy header handling
Firewall IP resolution MUST optionally use `X-Forwarded-For` only when proxy header trust is enabled and the socket source IP belongs to configured trusted proxy CIDR list.

#### Scenario: Trusted proxy source
- **WHEN** `firewall_trust_proxy_headers=true`, source socket IP matches trusted CIDR, and `X-Forwarded-For` is present
- **THEN** firewall uses first valid IP from `X-Forwarded-For`

#### Scenario: Untrusted proxy source
- **WHEN** source socket IP is outside trusted CIDR list
- **THEN** firewall ignores `X-Forwarded-For` and uses socket client IP
