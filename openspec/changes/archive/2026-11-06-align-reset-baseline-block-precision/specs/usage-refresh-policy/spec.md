## ADDED Requirements

### Requirement: Recovery baseline selection uses persisted block timestamp precision

Persisted reset-evidence discovery MUST apply the same whole-second post-block boundary as guarded account recovery. A usage sample whose UTC epoch second is equal to or earlier than the persisted `blocked_at` MUST NOT be selected as a recovery baseline. An ineligible same-second sample MUST NOT shadow a later eligible baseline with the same reset deadline.

#### Scenario: Fractional same-second baseline does not shadow valid weekly recovery
- **GIVEN** a rate-limited account has a future reset deadline and an elapsed minimum cooldown
- **AND** its earliest reset-matching usage sample was recorded fractionally after the start of the `blocked_at` second
- **AND** a later sample in a strictly later UTC epoch second matches the same reset deadline
- **AND** a qualifying adjacent reset transition follows that later baseline and latest usage remains available
- **WHEN** background refresh resolves and reconciles persisted reset evidence
- **THEN** it ignores the same-second sample and selects the later eligible baseline
- **AND** it recovers the account through the existing marker-guarded status transition

#### Scenario: Same-second evidence alone remains insufficient
- **GIVEN** every usage sample matching the persisted reset deadline is in or before the `blocked_at` second
- **WHEN** background refresh resolves persisted reset evidence
- **THEN** it does not produce recovery evidence for that block
- **AND** a later available-usage sample alone does not override the persisted cooldown
