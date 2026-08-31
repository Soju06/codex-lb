## ADDED Requirements

### Requirement: Dashboard OAuth polls ignore stale generations

The dashboard OAuth client MUST isolate in-flight status polls and start continuations by a monotonic generation that reset and restart invalidate. A poll MUST capture the flow ID and completion credentials before awaiting status or completion. After each of those awaits, the client MUST continue only when the generation is still current and the captured flow ID still identifies the live flow. After awaiting OAuth start, the client MUST apply the new flow only when that start's generation is still current. A fenced poll or start MUST NOT complete OAuth, MUST NOT write success or error onto a newer flow, and MUST NOT invalidate account or dashboard caches. An uninterrupted current-flow poll MUST still apply success, error, and pending results as it does today.

#### Scenario: Stale successful poll after reset and restart is ignored

- **GIVEN** dashboard OAuth flow A is pending and a status poll for A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** the in-flight poll for A later resolves as success
- **THEN** the client does not call OAuth completion for A or B
- **AND** it does not mark flow B success or error
- **AND** it does not invalidate account or dashboard caches
- **AND** flow B remains the live pending flow

#### Scenario: Stale error poll after reset and restart is ignored

- **GIVEN** dashboard OAuth flow A is pending and a status poll for A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** the in-flight poll for A later resolves as error
- **THEN** the client does not write A's error onto flow B
- **AND** flow B remains the live pending flow

#### Scenario: Stale start success after reset and restart is ignored

- **GIVEN** dashboard OAuth start A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** start A later resolves as success
- **THEN** the client does not replace flow B with A's credentials
- **AND** flow B remains the live pending flow

#### Scenario: Stale start error after reset and restart is ignored

- **GIVEN** dashboard OAuth start A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** start A later fails
- **THEN** the client does not write A's error onto flow B
- **AND** flow B remains the live pending flow

#### Scenario: Current-flow poll success still completes

- **GIVEN** dashboard OAuth flow A is pending and no reset or restart has occurred
- **WHEN** a status poll for A resolves as success and completion succeeds
- **THEN** the client completes flow A
- **AND** it marks the live flow success
- **AND** it invalidates account and dashboard caches
