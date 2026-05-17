# API Firewall — Delta

## ADDED Requirements

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
