## ADDED Requirements

### Requirement: Subscription-backed transcription reservations survive cancellation safely

The system MUST reserve API-key usage before forwarding an authenticated subscription-backed transcription request, and MUST release that owned reservation exactly once when cancellation interrupts upstream forwarding. The cancellation-deferring release MUST finish despite active AnyIO cancellation. If release persistence succeeds, the reservation MUST reach `released` state and its reserved quota MUST be restored before the original cancellation propagates. If release persistence fails after the existing bounded persistence retries, the system MUST emit cancellation-neutral cleanup diagnostics, MUST propagate the original cancellation, and MUST leave the reservation eligible for stale-reservation reclamation.

#### Scenario: Cancelled subscription transcription releases its reservation

- **GIVEN** a limited API key has created an owned reservation for a subscription-backed transcription request
- **WHEN** cancellation interrupts the request while upstream transcription forwarding is in flight
- **THEN** the request owner finishes releasing the reservation exactly once despite active cancellation
- **AND** the reservation reaches `released` state and its reserved quota is restored
- **AND** the original cancellation propagates after cleanup completes
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Failed cancellation release remains recoverable

- **GIVEN** cancellation interrupts a limited subscription-backed transcription request after its reservation is created
- **AND** the immediate release attempt exhausts the existing bounded persistence retries
- **WHEN** release persistence reports failure
- **THEN** the proxy emits cancellation-neutral cleanup diagnostics
- **AND** the original cancellation propagates
- **AND** stale-reservation reclamation remains eligible to release the reservation and restore its reserved quota
