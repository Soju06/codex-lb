## ADDED Requirements

### Requirement: Codex review label sync review-thread state

The Codex label synchronization script MUST treat unresolved, non-outdated
Codex inline review findings on the current head as needs-work evidence. It
MUST NOT treat inline Codex findings from resolved or outdated review threads as
active needs-work evidence.

#### Scenario: Resolved inline finding no longer blocks the ok label

- **WHEN** a current-head inline Codex finding comment belongs to a resolved
  review thread
- **AND** a clean current-head Codex review exists
- **THEN** the script does not classify that inline finding as active
  needs-work evidence

#### Scenario: Unresolved inline finding still blocks the ok label

- **WHEN** a current-head inline Codex finding comment belongs to an unresolved,
  non-outdated review thread
- **THEN** the script classifies that inline finding as active needs-work
  evidence
