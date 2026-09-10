## ADDED Requirements

### Requirement: Usage-exhausted failover retires only soft affinity

When a pre-visible quota failure proves that a selected account has exhausted
usage, automatic quota failover MAY retire the current request's prompt-cache
or sticky-thread mapping only when that mapping still points to the rejected
account. This path MUST NOT delete or rebind hard Codex-session, turn-state,
previous-response, file, or other continuity ownership. A concurrent mapping
reassignment MUST win over the cleanup.

#### Scenario: Exhausted prompt-cache owner is retired

- **GIVEN** a prompt-cache mapping points to account A
- **WHEN** A returns `usage_limit_reached` before response visibility and quota
  failover is enabled
- **THEN** the mapping is cleared if it still points to A
- **AND** ordinary selection can establish soft affinity to a replacement

#### Scenario: Generic error leaves the soft mapping intact

- **WHEN** the selected account returns an error that does not prove usage
  exhaustion
- **THEN** quota failover cleanup does not remove its soft mapping

#### Scenario: Concurrent reassignment is preserved

- **GIVEN** a soft mapping pointed to A when its request began
- **AND** another operation reassigns that mapping to B
- **WHEN** A's exhaustion cleanup executes
- **THEN** the compare-and-set cleanup is a no-op
- **AND** the mapping remains assigned to B

#### Scenario: Disabled setting preserves soft affinity

- **GIVEN** automatic quota failover is disabled
- **WHEN** a selected soft owner returns a usage-exhaustion response
- **THEN** this feature does not retire the soft mapping
