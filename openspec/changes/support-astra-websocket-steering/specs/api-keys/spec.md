## ADDED Requirements

### Requirement: Queued steering extends one reserved usage reservation

The proxy SHALL extend an existing reserved API-key usage reservation when
additional owned WebSocket steering input is admitted onto the same
successor. It SHALL lock that reservation row before adjusting limit
rows. Rejection SHALL reduce only the unapplied input increment. A failed
reduction SHALL leave the conservative reservation for terminal
settlement. Ordinary finalize and release paths SHALL keep their existing
lock behavior.

#### Scenario: Additional steering input extends the successor reservation

- **GIVEN** a successor already holds a reserved usage reservation
- **WHEN** another valid steer is admitted onto that successor
- **THEN** the reservation's input budget is extended before upstream dispatch

#### Scenario: A rejected steer reduces only its increment

- **GIVEN** several admitted steering submissions share one reservation
- **WHEN** one submission is rejected before it is applied
- **THEN** only that submission's unapplied increment is released
