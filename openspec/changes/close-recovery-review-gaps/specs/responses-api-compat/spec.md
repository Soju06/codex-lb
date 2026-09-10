## ADDED Requirements

### Requirement: Equivalent recovery identity and safe replay classification

Recovery SHALL canonicalize dictionary-valued serialized turn metadata regardless
of installation-ID presence. Eligible proxy-injected stale-anchor retries SHALL
consume their verified fresh body exactly once and reconnect without settling
the original error. Identity-bearing errors SHALL remain ineligible.

#### Scenario: Reordered nested turn metadata

- **WHEN** equivalent turn metadata strings differ only in dictionary key order
- **THEN** the operation fingerprints SHALL match

#### Scenario: Existing operation stored with legacy metadata spelling

- **WHEN** an anchored retry matches an operation fingerprint from either historical outer-key ordering mode
- **THEN** lookup SHALL retain the stored operation identity without relaxing session, parent, or API-key fences

#### Scenario: Completed root stored with legacy metadata spelling

- **WHEN** a session-scoped root operation completed before nested metadata canonicalization changed
- **THEN** an equivalent hard-continuity submission SHALL find the legacy session-scoped fingerprint and advance from its completed response instead of dispatching a second root
- **AND** legacy lookup SHALL NOT reuse an operation from another session or API-key scope

#### Scenario: Proxy-injected terse stale anchor

- **WHEN** an opted-in identity-free stale-anchor error has a verified self-contained fresh body
- **THEN** the websocket SHALL reconnect with one replay and suppress the original error

### Requirement: Deterministic response operation selection

Transcript lookups SHALL select operations by descending update time and then
ascending operation ID when multiple terminal operations share a response ID.

#### Scenario: Tied timestamps

- **WHEN** two terminal operations have equal response IDs and update timestamps
- **THEN** lookup SHALL select the same operation independently of insertion order
