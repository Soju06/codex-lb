## ADDED Requirements

### Requirement: Reset and guest-generation composition preserves authentication state

The database MUST upgrade to one head from either the published reset/spool merge or guest-generation revision. Upgrade and merge-only downgrade MUST preserve existing reset bindings, reset policy, admin and guest credentials, guest-access state, retention settings and nonzero guest generations. A database without the generation column MUST receive the existing zero default through the guest parent migration. Existing guest revocation and protected-search permissions MUST remain unchanged.

#### Scenario: Reset database adopts guest generation

- **WHEN** a populated reset/spool database upgrades to the composition head
- **THEN** exact reset owner and credit bindings and stored settings remain unchanged
- **AND** guest generation is initialized to zero

#### Scenario: Guest generation survives composition rollback

- **WHEN** a database with a nonzero guest generation upgrades and downgrades only the composition merge
- **THEN** the stored generation and credentials remain unchanged
- **AND** both direct parent revisions remain stamped
