## ADDED Requirements

### Requirement: Provisional probe affinity compare and set

A provisional probe affinity write MUST preserve a binding committed after its owner lookup. Probe cleanup MUST restore affinity only after the probe has confirmed its own write. Cleanup MUST propagate request cancellation even when affinity rollback fails.

#### Scenario: Another request rebinds affinity before the probe writes

- **GIVEN** a recovery probe has observed the current affinity
- **WHEN** another request changes that binding before the probe writes
- **THEN** the probe preserves the newer binding and retries selection

#### Scenario: Same-account affinity refresh shares a clock tick

- **WHEN** a normal affinity refresh writes the same owner during the same clock tick as its previous write
- **THEN** a probe using the earlier observation cannot overwrite it

#### Scenario: A competing binding wins while a probe write is cancelled

- **WHEN** cancellation interrupts the probe's wait for its provisional write
- **AND** a different request won the write for that key
- **THEN** cleanup releases the probe's admission and leaves the winning binding in place

#### Scenario: Affinity rollback fails during cancellation

- **WHEN** a cancelled recovery probe releases its lease and reservation
- **AND** restoring its provisional affinity fails
- **THEN** the original cancellation propagates and the rollback error is logged
