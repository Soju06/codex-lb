## MODIFIED Requirements

### Requirement: Access-rejection evidence survives persistence conflicts and unrelated repairs

Refresh-token persistence-conflict handling MUST preserve an existing proven access-rejection reason while the rejected credentials remain current. A repair of account B MUST NOT suppress a successful rejection mark for account A. Only explicit repair evidence for A MUST fence its stale marks; a snapshot rebuild or cache reset alone MUST NOT suppress a committed rejection mark.

#### Scenario: Persistence conflict meets an existing rejection
- **WHEN** guarded token persistence exhausts retries while the current row retains proven access rejection
- **THEN** the returned and persisted account retain the blocking rejection reason

#### Scenario: An unrelated account is repaired during rejection persistence
- **WHEN** B is repaired while A's guarded rejection write succeeds
- **THEN** A is immediately unavailable to stale live bridge reuse without waiting for the next invalidation poll

#### Scenario: A stale snapshot is published before a committed rejection mark
- **WHEN** a routing snapshot reads A before its rejection commits and is published before the local mark is applied
- **THEN** the mark MUST immediately block stale bridge reuse without requiring another invalidation poll

#### Scenario: Same-account repair spans snapshot refresh or reset
- **WHEN** an explicit repair of A invalidates a pending older rejection mark
- **AND** a snapshot refresh or cache reset occurs before that mark is applied
- **THEN** the stale mark MUST remain suppressed

## ADDED Requirements

### Requirement: Credential repair preserves active reset-based cooldowns

Replacing rejected access-token material MUST NOT make an account with an unexpired persisted reset-based cooldown selectable before its reset. Credential repair MUST preserve the reset deadline and independent operator restrictions while removing the repaired authentication rejection. After the reset expires, existing routing recovery rules MUST govern eligibility.

#### Scenario: Rate limiting precedes rejection and repair
- **WHEN** rate limiting persists a future reset deadline before access rejection replaces the routing status
- **AND** guarded token rotation repairs the rejected access credentials
- **THEN** selection MUST still exclude that account until the cooldown expires
- **AND** rejection persistence and repair MUST NOT overwrite unrelated cooldown timestamps
