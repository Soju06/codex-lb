## MODIFIED Requirements

### Requirement: Reservation 정산 exactly-once 보장

Usage reservation의 최종 정산(finalize 또는 release)은 요청 단위에서 정확히 1회 수행되어야 한다. 재시도 가능한 중간 attempt에서는 정산을 defer하고, 요청 종료 시점에서 단일 지점이 정산 책임을 갖는다. 시스템은 이 동작을 SHALL 보장해야 한다.

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

### Requirement: 조기 종료 경로에서 reservation release 보장

Reservation 생성 후 upstream API 호출에 진입하지 않고 종료되는 모든 경로에서 reservation이 release되어야 한다. `reserved` 상태로 남는 reservation이 존재하면 안 된다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: no_accounts 즉시 종료 시 release

- **WHEN** reservation 생성 후 `_stream_with_retry()`가 사용 가능한 계정 없음(`no_accounts`)으로 즉시 종료되면
- **THEN** `release_usage_reservation()`이 호출되어 reservation이 `released` 상태로 전이되어야 한다 (SHALL)
- **AND** pre-reserved quota가 원복되어야 한다 (SHALL)

#### Scenario: 재시도 소진 후 no_accounts 종료 시 release

- **WHEN** 재시도 루프가 모든 attempt를 소진한 후 `no_accounts`로 종료되면
- **THEN** `release_usage_reservation()`이 호출되어야 한다 (SHALL)

#### Scenario: reservation 미생성 시 정산 스킵

- **WHEN** API key auth가 비활성이거나 reservation이 생성되지 않은 상태에서 요청이 종료되면
- **THEN** 정산 로직이 안전하게 스킵되어야 하며 에러가 발생하지 않아야 한다 (SHALL)

### Requirement: Compact 경로 예외 무관 reservation cleanup

`_compact_responses()` 경로에서 reservation이 존재할 때, 어떤 예외 타입이 발생하더라도 reservation이 정리되어야 한다. 특정 예외 타입에만 의존하는 cleanup은 허용되지 않는다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: ProxyResponseError 발생 시 release

- **WHEN** `compact_responses()`에서 `ProxyResponseError`가 발생하면
- **THEN** reservation이 release되어야 한다 (SHALL)

#### Scenario: 예상 외 런타임 예외 발생 시 release

- **WHEN** `compact_responses()`에서 `ProxyResponseError` 외의 예외(`Exception`)가 발생하면
- **THEN** reservation이 동일하게 release되어야 한다 (SHALL)

#### Scenario: compact 성공 시 finalize

- **WHEN** `compact_responses()`가 정상 완료되면
- **THEN** `finalize_usage_reservation()`이 호출되어야 한다 (SHALL)

### Requirement: Finalize / Release 멱등성

`finalize_usage_reservation()`과 `release_usage_reservation()`은 이미 정산된(finalized 또는 released) reservation에 대해 안전하게 no-op 처리되어야 한다. 이중 호출이 quota를 이중 반영하거나 에러를 발생시키면 안 된다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: finalize 후 release 호출 시 no-op

- **WHEN** reservation이 이미 `finalized` 상태에서 `release_usage_reservation()`이 호출되면
- **THEN** 아무 동작 없이 반환되어야 한다 (SHALL)
- **AND** quota 값이 변경되지 않아야 한다 (SHALL)

#### Scenario: release 후 finalize 호출 시 no-op

- **WHEN** reservation이 이미 `released` 상태에서 `finalize_usage_reservation()`이 호출되면
- **THEN** 아무 동작 없이 반환되어야 한다 (SHALL)
- **AND** quota 값이 변경되지 않아야 한다 (SHALL)

#### Scenario: 동일 finalize 이중 호출 시 1회만 반영

- **WHEN** 동일 `reservation_id`로 `finalize_usage_reservation()`이 2회 호출되면
- **THEN** 사용량은 정확히 1회만 반영되어야 한다 (SHALL)
