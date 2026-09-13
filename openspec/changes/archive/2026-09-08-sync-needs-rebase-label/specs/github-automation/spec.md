## ADDED Requirements

### Requirement: Needs-rebase label sync

The Codex label synchronization script MUST add `needs rebase` when GitHub
reports a confirmed merge conflict, MUST remove it when GitHub reports a known
mergeable or non-conflict state, and MUST preserve its current value only when
merge state is unknown. It MUST NOT infer a conflict from the pull request
merely being behind the base branch.

#### Scenario: Confirmed conflict gains the label

- **WHEN** GitHub reports the pull request as `CONFLICTING` or `DIRTY`
- **THEN** the synchronizer adds `needs rebase`

#### Scenario: Review-blocked pull request loses a stale label

- **GIVEN** a pull request has `needs rebase`
- **WHEN** GitHub reports it as `BLOCKED` by review or status requirements
- **THEN** the synchronizer removes `needs rebase`

#### Scenario: Mergeable status loses a stale label

- **GIVEN** a pull request has `needs rebase`
- **WHEN** GitHub reports `BEHIND`, `CLEAN`, `DRAFT`, `HAS_HOOKS`, or `UNSTABLE`
- **THEN** the synchronizer removes `needs rebase`

#### Scenario: Unknown state preserves current evidence

- **WHEN** GitHub reports an unknown merge state
- **THEN** the synchronizer preserves the current `needs rebase` value
