## ADDED Requirements

### Requirement: Relative retry from a retained repair checkpoint

Upgrade tooling MUST preserve the resolved target of a relative request started while a historical usage-limit repair checkpoint remains.

#### Scenario: A pending relative upgrade applies before interruption

- **GIVEN** a historical repair checkpoint remains after an earlier upgrade
- **WHEN** a relative upgrade reaches its target but stops before clearing the repair state
- **THEN** retrying the same relative request stays at that target
