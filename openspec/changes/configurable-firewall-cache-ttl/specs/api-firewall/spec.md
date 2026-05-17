## ADDED Requirements

### Requirement: Firewall decision cache TTL is operator-configurable
The application SHALL expose the TTL of the per-IP firewall decision cache
as a configuration setting. The setting SHALL default to the existing
hard-coded value so unconfigured deployments observe no behavior change,
and SHALL reject non-positive values at startup.

#### Scenario: Default TTL when unset
- **WHEN** `firewall_cache_ttl_seconds` is not set in the environment
- **THEN** the process-level firewall decision cache uses a TTL of `2` seconds

#### Scenario: Operator-configured TTL
- **WHEN** `firewall_cache_ttl_seconds` is set to a positive integer
- **THEN** the process-level firewall decision cache uses that value as its TTL

#### Scenario: Invalid TTL rejected at startup
- **WHEN** `firewall_cache_ttl_seconds` is set to `0` or a negative value
- **THEN** settings construction raises a validation error
