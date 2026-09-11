## ADDED Requirements

### Requirement: Queued steering extends one reserved usage reservation

The proxy SHALL extend an existing reserved API-key usage reservation when
additional owned WebSocket steering input is admitted onto the same
successor. It SHALL lock that reservation row before adjusting limit
rows. Rejection SHALL reduce only the unapplied input increment. A failed
reduction SHALL leave the conservative reservation for terminal
settlement. Ordinary finalize and release paths SHALL keep their existing
lock behavior.

Every additional submission SHALL reconcile the currently applicable API-key
limits, including limits added or activated after the first submission. When
no reservation exists, admission SHALL create one if limits now apply. Missing
reservation items SHALL reserve the queued successor's input and single output
budget under the existing request-admission policy; existing items SHALL extend
only the new input increment. An exhausted newly applicable limit SHALL reject
the new submission before dispatch without disturbing admitted work. Reconciliation
SHALL keep one successor reservation and its existing terminal settlement owner.

#### Scenario: A limit becomes applicable after unmetered steering
- **GIVEN** the first steer has no applicable quota reservation
- **WHEN** a new applicable limit is exhausted before another submission
- **THEN** the additional steer SHALL be rejected before upstream dispatch
- **AND** the original steer SHALL retain its successor lifecycle

#### Scenario: A new limit is added to an already reserved successor
- **GIVEN** queued steering has a reservation for an existing limit
- **WHEN** another submission is admitted after a new applicable limit is added
- **THEN** the same reservation SHALL gain an item for that limit before dispatch
- **AND** terminal settlement SHALL charge actual successor usage once to each item

#### Scenario: Additional steering input extends the successor reservation

- **GIVEN** a successor already holds a reserved usage reservation
- **WHEN** another valid steer is admitted onto that successor
- **THEN** the reservation's input budget is extended before upstream dispatch

#### Scenario: A rejected steer reduces only its increment

- **GIVEN** several admitted steering submissions share one reservation
- **WHEN** one submission is rejected before it is applied
- **THEN** only that submission's unapplied increment is released

### Requirement: Reservation lifecycle uses current ORM accounting values
Reservation reconciliation SHALL refresh reservation items after claiming settlement or release even when the session already holds those ORM entities. A locked steering adjustment SHALL refresh the current reservation status and item deltas before computing a budget change.

#### Scenario: Extension changed a retained reservation item
- **GIVEN** the session retains a previously read reservation and its items
- **WHEN** extension changes the stored reserved delta before settlement or release claims the reservation
- **THEN** reconciliation SHALL use the current stored delta and leave exactly actual usage charged

#### Scenario: Consecutive steering adjustments share a retained ORM identity
- **GIVEN** an adjustment session retains a previously read reservation and its items
- **WHEN** another session commits an extension or reduction before the adjustment acquires the reservation lock
- **THEN** the adjustment SHALL apply its delta to the current stored budget without losing the earlier change

#### Scenario: Terminal claim wins before a steering adjustment
- **GIVEN** an adjustment session retains a previously reserved reservation
- **WHEN** settlement or release commits before the adjustment acquires the reservation lock
- **THEN** the adjustment SHALL report that the reservation is no longer adjustable without charging or refunding any additional quota
