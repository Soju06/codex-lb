## ADDED Requirements

### Requirement: Relative availability zero-score fallback ranks by persisted usage

When every `relative_availability` candidate has a raw score of zero, the selector MUST rank candidates by persisted secondary-window usage, then persisted primary-window usage, both excluding runtime in-flight pressure, before applying any recency, account-id or selection-seed tie-break. A seeded caller MUST seed only among the candidates tied on that persisted-usage ranking.

#### Scenario: Zero-score fallback keeps the real usage difference

- **GIVEN** two eligible accounts whose relative-availability raw scores are both zero
- **AND** their persisted weekly usage is 95 and 38 percent
- **WHEN** account selection uses `relative_availability`
- **THEN** the 38 percent account is selected
- **AND** the result does not depend on which account was selected last

#### Scenario: Seeded fallback seeds only among equally used accounts

- **GIVEN** three eligible accounts whose relative-availability raw scores are all zero
- **AND** two of them have equal persisted usage below the third's
- **WHEN** a seeded caller selects with `relative_availability`
- **THEN** the pick is one of the two less-used accounts
