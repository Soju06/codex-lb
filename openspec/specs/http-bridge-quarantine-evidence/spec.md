# HTTP bridge quarantine evidence

## Purpose
Preserve process-local quarantine evidence across asynchronous cleanup and bounded admission.

## Requirements

### Requirement: Quarantine generations and owners preserve evidence lifetime
The proxy MUST allocate strictly increasing quarantine generations for the service lifetime, including first strikes, re-arms, revocations and downgrades. Pruning or replacing entries MUST NOT recycle an observed generation. Primary completion cleanup MUST match the canonical session when present, otherwise a weak owner reference; a detached predecessor MUST NOT clear a replacement's evidence. An absent captured generation MUST NOT authorize cleanup of a later entry, including same-key recovery.

#### Scenario: Old recovery completes after key reuse
- **GIVEN** recovery captured a quarantine generation and the entry was pruned and re-armed
- **WHEN** that recovery completes
- **THEN** the newer entry survives even when it uses the same key

#### Scenario: Detached predecessor completes
- **GIVEN** a replacement is canonical for a key while the predecessor still has a pending completion
- **WHEN** the predecessor completes
- **THEN** replacement quarantine and first-strike evidence survives

### Requirement: Cleanup preserves evidence acquired after authorization
Completion and recovery MUST capture quarantine provenance, raw generation and first-strike count before their first relevant await. Completion settlement MUST NOT recapture them. Active poison cleanup MUST match poison provenance while retaining concurrent weaker fences and first strikes recorded after capture. After poison expires, cleanup MUST match the captured raw generation. Revocation MUST use its arm-time evidence and MUST NOT remove a later first strike.

#### Scenario: Settlement races a first strike
- **GIVEN** completion captured poison evidence before settlement
- **WHEN** a first eventless strike is recorded during settlement
- **THEN** completion can clear the observed poison but retains the strike for the next timeout

#### Scenario: Poison appears during durable loading
- **GIVEN** completion observed no quarantine
- **WHEN** durable loading arms poison during completion
- **THEN** completion preserves that unobserved evidence without recapturing its fence
- **AND** after successful settlement and fresh-anchor registration, the next request's first-touch durable load MUST revoke the poison arm when it accepts a zero-failure row without an abandonment tombstone and the stale-load and newer-local-failure guards pass
- **AND** reuse selection MUST clear the stale session quarantine flag once no independent quarantine evidence remains
- **AND** failed settlement, unreadable durable state, an abandonment tombstone, or newer poison evidence MUST keep the relevant fence in force; independent weaker quarantine and later first-strike evidence MUST remain protected

### Requirement: Poison admission remains bounded and conservative
The registry MUST admit at most 1024 entries. It MUST evict non-poison entries deterministically by age, generation and key before refusing admission; active poison provenance MUST NOT be evicted before its own deadline. If all slots hold active poison evidence, a rejected poison arm MUST establish a service-level fail-closed deadline covering its required lifetime and retained active poison deadlines. Unknown keys and subsequently admitted weaker entries MUST remain poison-classified during that window. Rejection MUST be logged without exposing raw keys and MUST NOT prevent existing request retirement or failure handling. Quarantine MUST NOT write account health.

#### Scenario: Poison fills the registry
- **GIVEN** every registry slot contains active poison evidence
- **WHEN** another poison key is rejected
- **THEN** the registry does not grow or evict active poison
- **AND** the bounded overflow deadline prevents anchorless deltas from dispatching as new conversations

#### Scenario: A weaker entry is admitted during overflow
- **GIVEN** poison overflow is active and a registry slot becomes available
- **WHEN** an eventless first strike is admitted
- **THEN** its weaker entry cannot hide the active overflow poison classification
