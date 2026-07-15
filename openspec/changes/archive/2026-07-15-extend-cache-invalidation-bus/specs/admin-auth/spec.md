# admin-auth Delta

## ADDED Requirements

### Requirement: Security-bearing dashboard settings converge across replicas
Mutations to security-bearing dashboard settings (dashboard password hash, guest access and guest password, TOTP requirement, proxy API-key auth toggle) MUST durably bump the `settings` cache-invalidation namespace before the mutation response is returned, and every replica MUST re-read the settings row within the invalidation-bus poll bound. The per-process settings cache TTL (5s) is the documented fallback bound when a bump is lost.

#### Scenario: Enabling API-key auth on one replica is enforced on peers within one poll cycle

- **GIVEN** two replicas share one database and each runs the cache-invalidation poller
- **AND** replica B's settings cache was refreshed just before the change
- **WHEN** bootstrap or a settings mutation served by replica A sets a dashboard password and enables proxy API-key auth
- **THEN** after replica B's next poll cycle, replica B's settings cache reflects the new password hash and API-key auth toggle
- **AND** replica B rejects keyless proxy requests and unauthenticated dashboard requests without waiting for the settings TTL to expire
