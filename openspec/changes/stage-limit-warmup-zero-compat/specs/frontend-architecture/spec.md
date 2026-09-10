## ADDED Requirements

### Requirement: Settings schemas stage zero for mixed-version rollout

During the compatibility release, the settings response and update contracts
MUST accept `limit_warmup_exhausted_threshold_percent = 0.0` so a future
activation release can be rolled out beside this release. The dashboard control
MUST continue to reject values below `1` and MUST continue to render its
positive `99` default. A direct settings update containing `0.0` MUST be
accepted by this release but normalized to the legacy `99.0` representation in
the persisted row and returned response.

#### Scenario: Response schema accepts the future zero value

- **WHEN** a settings response contains an exhausted threshold of `0.0`
- **THEN** the client response schema accepts it

#### Scenario: Update schema accepts the future zero value

- **WHEN** a settings update contains an exhausted threshold of `0.0`
- **THEN** the client and backend update schemas accept it

#### Scenario: Dashboard remains a positive-value control

- **WHEN** the routing settings form renders the reset-confirmed threshold
- **THEN** its minimum remains `1`
- **AND** a zero value is not offered or saved by the form

#### Scenario: Compatibility update does not emit the sentinel

- **WHEN** a direct `PUT` settings request contains an exhausted threshold of
  `0.0`
- **THEN** the request succeeds
- **AND** the persisted legacy threshold and response are both `99.0`

