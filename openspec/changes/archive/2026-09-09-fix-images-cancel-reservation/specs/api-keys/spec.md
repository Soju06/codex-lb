## MODIFIED Requirements

### Requirement: Reservation 정산 exactly-once 보장

Usage reservation의 최종 정산(finalize 또는 release)은 요청 단위에서 정확히 1회 수행되어야 한다. 재시도 가능한 중간 attempt에서는 정산을 defer하고, 요청 종료 시점에서 단일 지점이 정산 책임을 갖는다. 시스템은 이 동작을 SHALL 보장해야 한다.

When an Images route owns a limited API-key reservation and cancellation
interrupts the first upstream SSE read, the system MUST close the upstream
iterator, MUST finish the route-owned release attempt despite active
cancellation, and MUST then propagate the original `CancelledError`. A failed
close or release MUST be logged and MUST NOT replace the original cancellation.
Stale reclamation MUST remain an exceptional backstop and MUST NOT substitute
for normal request-owned cleanup.

#### Scenario: 스트림 401 → refresh retry 성공 시 finalize 1회

- **WHEN** 첫 `_stream_once()` attempt에서 401을 수신하고 계정 refresh 후 재시도가 성공하면
- **THEN** 첫 attempt에서는 reservation 정산이 수행되지 않아야 한다 (SHALL)
- **AND** 최종 성공 시점에서 `finalize_usage_reservation()`이 정확히 1회 호출되어야 한다 (SHALL)
- **AND** 실제 token 사용량이 quota에 반영되어야 한다 (SHALL)

#### Scenario: 스트림 401 → retry 소진 실패 시 release 1회

- **WHEN** 401 후 재시도를 모두 소진하여 요청이 최종 실패하면
- **THEN** `release_usage_reservation()`이 정확히 1회 호출되어야 한다 (SHALL)
- **AND** 예약된 quota가 원복되어야 한다 (SHALL)

#### Scenario: 스트림 성공 시 finalize 1회

- **WHEN** `_stream_once()`가 retry 없이 첫 attempt에서 성공하면
- **THEN** `finalize_usage_reservation()`이 정확히 1회 호출되어야 한다 (SHALL)

#### Scenario: Cancelled Images priming releases its route-owned reservation

- **GIVEN** a limited API key has created an Images route-owned reservation for
  `/v1/images/generations` or `/v1/images/edits`
- **AND** the internal Responses stream has no API-key reservation owner
- **WHEN** request cancellation interrupts the first upstream SSE read before
  any event is yielded
- **THEN** the upstream iterator is closed
- **AND** the Images route finishes releasing its reservation exactly once
  despite active cancellation
- **AND** the reservation reaches `released` state and its reserved quota is
  restored
- **AND** the original `CancelledError` propagates after cleanup completes
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Failed upstream close does not prevent reservation release

- **GIVEN** cancellation interrupts Images stream priming before the first
  upstream SSE event
- **WHEN** closing the upstream iterator fails but the route-owned reservation
  release succeeds
- **THEN** the proxy logs the close failure
- **AND** the Images route releases its reservation exactly once
- **AND** the reservation reaches `released` state and its reserved quota is
  restored
- **AND** the original `CancelledError` propagates unchanged
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Failed reservation release preserves the original cancellation

- **GIVEN** cancellation interrupts Images stream priming before the first
  upstream SSE event
- **WHEN** releasing the route-owned reservation fails
- **THEN** the proxy logs the release failure
- **AND** the original `CancelledError` propagates unchanged
- **AND** the still-reserved reservation remains eligible for stale reclamation
