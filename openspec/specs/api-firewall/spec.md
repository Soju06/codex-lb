# api-firewall Specification

## Purpose
Governs the IP allowlist that gates the proxy surfaces (`/backend-api/codex/*`, `/v1/*`) and the dashboard API for managing it. Operators who expose codex-lb beyond a private network need to restrict which client addresses may reach the proxy, including deployments behind a reverse proxy where the client IP must be derived from trusted forwarding headers. This capability defines allowlist management, enforcement on protected paths, trusted-proxy header handling, and the safety defaults around header trust and cache TTL.
## Requirements
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

For protected HTTP and WebSocket requests, firewall IP resolution MUST use the captured pre-projection raw socket peer as its socket source. It MUST NOT fall back to the downstream projected client when capture is absent. With proxy-header trust disabled, firewall identity MUST be the raw socket peer. With proxy-header trust enabled, the firewall MUST use forwarded client headers only when the raw socket peer belongs to the configured trusted proxy CIDR list. `X-Forwarded-For` and RFC 7239 `Forwarded` values MUST be resolved from right to left through one shared trusted-hop algorithm. Other client-IP headers, including `X-Real-IP`, `True-Client-IP`, and `CF-Connecting-IP`, MUST NOT affect firewall identity. Every repeated field value MUST be combined in arrival order before resolution. A missing raw-peer capture or a missing, malformed, ambiguous, obfuscated, unknown, or non-IP trusted-proxy chain MUST return no resolved client IP so an active firewall allowlist fails closed. An empty allowlist MUST remain allow-all. Downstream consumers MUST continue to observe the projected client and scheme.

#### Scenario: Proxy trust disabled ignores a listed projection

- **WHEN** proxy-header trust is disabled
- **AND** the captured raw peer is not allowlisted
- **AND** proxy projection replaces the downstream client with an allowlisted address
- **THEN** both protected HTTP and WebSocket firewall enforcement deny the request
- **AND** downstream projection does not determine firewall identity

#### Scenario: Proxy trust disabled allows a listed raw peer

- **WHEN** proxy-header trust is disabled
- **AND** the captured raw peer is allowlisted
- **AND** proxy projection replaces the downstream client with an unlisted address
- **THEN** both protected HTTP and WebSocket firewall enforcement allow the request
- **AND** downstream consumers continue to observe the projected client and scheme

#### Scenario: Untrusted raw peer cannot establish forwarded trust

- **WHEN** proxy-header trust is enabled
- **AND** the captured raw peer is outside configured trusted CIDRs and is not allowlisted
- **AND** proxy projection replaces the downstream client with a trusted, allowlisted address
- **THEN** both protected HTTP and WebSocket firewall enforcement deny the request

#### Scenario: Missing capture fails closed with active allowlist

- **WHEN** raw-peer capture is absent
- **AND** the allowlist contains one or more entries
- **THEN** both protected HTTP and WebSocket firewall enforcement deny the request
- **AND** the projected client is not used as fallback identity

#### Scenario: Missing capture preserves empty allow-all mode

- **WHEN** raw-peer capture is absent
- **AND** the allowlist is empty
- **THEN** both protected HTTP and WebSocket firewall enforcement allow the request

#### Scenario: Trusted proxy chain

- **WHEN** `firewall_trust_proxy_headers=true`
- **AND** the captured raw socket peer and downstream proxy hops match configured trusted CIDRs
- **AND** a valid `X-Forwarded-For` or `Forwarded` chain is present
- **THEN** the firewall resolves the originating client by traversing the chain from right to left

#### Scenario: Trusted proxy appends a separate field

- **WHEN** a client supplies a spoofed loopback value in the first `X-Forwarded-For` or `Forwarded` field
- **AND** a trusted raw socket proxy appends the actual remote client in a second field
- **THEN** the firewall combines both fields in arrival order
- **AND** resolves the actual remote client rather than the spoofed loopback value

#### Scenario: Singleton proxy headers do not authorize firewall access

- **WHEN** a trusted raw socket proxy supplies only `X-Real-IP`, `True-Client-IP`, or `CF-Connecting-IP`
- **THEN** firewall client resolution returns no client IP
- **AND** an active firewall allowlist denies the request

#### Scenario: Untrusted proxy source

- **WHEN** the captured raw socket peer is outside the configured trusted CIDR list
- **THEN** the firewall ignores forwarded client headers
- **AND** uses the raw socket peer IP

#### Scenario: Trusted source supplies no complete valid chain

- **WHEN** proxy-header trust is enabled and the captured raw socket peer is trusted
- **AND** the forwarded client chain is missing or contains an invalid hop
- **THEN** firewall client resolution returns no client IP
- **AND** an active firewall allowlist denies the request

### Requirement: Firewall IP cache TTL is operator-configurable with a safe default

The application MUST cache firewall allow/deny decisions per source IP for a configurable TTL, and the default TTL MUST be large enough (at least 30 seconds) that the cache provides material relief on hot paths under load. Operators MUST be able to tune it via `firewall_ip_cache_ttl_seconds` (env `CODEX_LB_FIREWALL_IP_CACHE_TTL_SECONDS`). Explicit cache invalidation paths (allowlist mutation in `/api/firewall/ips`, the `cache_poller` invalidation channel) MUST keep working unchanged.

#### Scenario: Default TTL provides effective caching

- **WHEN** the application starts with no override
- **THEN** `FirewallIPCache.ttl_seconds == 30`
- **AND** a hot-path proxy request whose source IP has been seen within the last 30 seconds does NOT open a DB session for the firewall check

#### Scenario: Operator override is honoured

- **WHEN** `CODEX_LB_FIREWALL_IP_CACHE_TTL_SECONDS=120` is set
- **AND** the application starts
- **THEN** the firewall cache TTL is 120 seconds

#### Scenario: Allowlist mutation invalidates the cache immediately

- **WHEN** an operator adds or removes an entry via `POST /api/firewall/ips` or `DELETE /api/firewall/ips/{ip}`
- **THEN** the firewall cache is invalidated for all IPs before the API response is returned
- **AND** the next request from any IP re-checks the database

### Requirement: Enabled proxy-header trust requires source configuration

The application MUST fail settings validation when `firewall_trust_proxy_headers` is enabled and the normalized `firewall_trusted_proxy_cidrs` list is empty. The validation MUST apply independently of dashboard authentication mode and MUST identify the conflicting settings. An empty trusted-proxy CIDR list MUST remain valid while proxy-header trust is disabled.

#### Scenario: Enabled trust with empty CIDRs fails startup

- **WHEN** `firewall_trust_proxy_headers=true`
- **AND** `firewall_trusted_proxy_cidrs` is empty or contains only whitespace and delimiters
- **THEN** settings validation fails before the application starts
- **AND** the error identifies that enabled proxy-header trust requires at least one trusted-proxy CIDR

#### Scenario: Disabled trust permits an empty CIDR list

- **WHEN** `firewall_trust_proxy_headers=false`
- **AND** `firewall_trusted_proxy_cidrs` normalizes to an empty list
- **THEN** settings validation succeeds
- **AND** forwarded client-IP headers remain untrusted

