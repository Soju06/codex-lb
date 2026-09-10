## ADDED Requirements

### Requirement: Interior output echoes have a unique alignment

Complete-transcript recovery MUST remove an interior echoed output subsequence
only when exactly one matching occurrence exists after replay normalization.
Ambiguous occurrences MUST fail closed before operation rebind or upstream replay.

#### Scenario: Two equivalent interior echoes

- **WHEN** an unanchored continuation has a fresh prefix followed by multiple
  occurrences matching the persisted output after provider IDs are sanitized
- **THEN** reconstruction is rejected without rebinding or dispatching recovery

#### Scenario: Unique interior echo

- **WHEN** exactly one interior occurrence matches the persisted output
- **THEN** reconstruction retains the surrounding fresh input and includes the
  persisted output once
