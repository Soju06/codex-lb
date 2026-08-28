# outbound-http-clients Delta

## ADDED Requirements

### Requirement: Upstream connectors persist across interactive turn gaps

The shared upstream TCP connectors MUST configure connection keepalive of at least 90 seconds and a DNS cache TTL of at least 300 seconds, so consecutive interactive requests reuse pooled connections and resolved names instead of re-handshaking per turn.

#### Scenario: Connector construction pins reuse settings

- **WHEN** the shared HTTP client initializes its direct TCP connectors
- **THEN** they are constructed with `keepalive_timeout >= 90` and `ttl_dns_cache >= 300`
