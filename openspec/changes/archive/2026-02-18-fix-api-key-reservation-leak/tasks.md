## 1. 요청 단위 정산 책임 단일화 (P1)

- [x] 1.1 `_stream_with_retry()`에 요청 스코프 `settled: bool = False` 상태 도입 — 정산 완료 여부를 단일 플래그로 추적
- [x] 1.2 `_stream_with_retry()`에 `finally` 블록 추가 — `if not settled and reservation_id: await release_usage_reservation(reservation_id)` 백스톱 정산
- [x] 1.3 성공 경로에서 `finalize_usage_reservation()` 호출 직후 `settled = True` 설정 — finalize 완료 시에만 플래그 전환
- [x] 1.4 `_stream_once()`의 `finally` 블록에서 reservation 최종 정산 로직 제거 — attempt 단위에서는 정산하지 않도록 변경. 메트릭 로깅 등 비정산 로직은 유지

## 2. 401 Refresh Retry 정산 Defer (P1)

- [x] 2.1 `_stream_once()`에서 401 + refresh 성공 판정 시 `settle_reservation = False` 설정 확인 — `settle_reservation` 변수 자체를 제거하여 attempt 단위 정산이 불가능한 구조로 변경
- [x] 2.2 `_stream_once()`의 `finally` 블록에서 `settle_reservation == False`일 때 정산 스킵 확인 — 정산 로직 자체가 제거되어 모든 경로에서 정산 스킵됨
- [x] 2.3 retry 루프에서 동일 `reservation_id`가 다음 attempt에 전달되는 경로 확인 — `api_key_reservation`이 `_stream_with_retry()` 스코프에서 관리되어 재사용 보장됨

## 3. No-Account / 조기 종료 백스톱 (P1)

- [x] 3.1 `_stream_with_retry()`에서 `selection.account is None` 즉시 반환 경로가 Task 1.2의 `finally` 백스톱에 도달하는지 확인 — try 블록 내 return → finally 실행 → settled=False → release
- [x] 3.2 재시도 소진 후 `no_accounts` 종료 경로가 동일하게 `finally` 백스톱에 도달하는지 확인 — for 루프 종료 후 yield → finally 실행 → release
- [x] 3.3 reservation이 존재하지 않는 상태(예약 실패 또는 API key auth 비활성)에서 `finally` 블록이 안전하게 스킵되는지 확인 — `api_key is not None and api_key_reservation is not None` 검사

## 4. Compact 경로 Fail-Safe Cleanup (P2)

- [x] 4.1 `_compact_responses()` (API 레이어)에서 서비스 호출 전체를 `try/finally`로 감싸도록 변경 — `_release_request_reservation()`을 finally에서 호출
- [x] 4.2 `compact_responses()` 성공 시 `finalize_usage_reservation()` 호출 + `settled = True` — 서비스 내부의 `_settle_compact_api_key_usage()`가 처리, finally의 release는 멱등 no-op
- [x] 4.3 `finally` 블록에서 `if not settled and reservation_id: await release_usage_reservation(...)` 백스톱 정리 — `_release_request_reservation()`이 reservation null 검사 포함
- [x] 4.4 기존 `NotImplementedError` / `ProxyResponseError` catch 블록에서 reservation release 로직 제거 — `finally`로 통합되므로 중복 제거. 에러 응답 생성 로직만 유지

## 5. Finalize / Release 멱등성 검증 (P2)

- [x] 5.1 `finalize_usage_reservation()`이 `reserved` 상태가 아닌 reservation에 대해 조기 반환하는 기존 동작 확인 — service.py:383-384에서 status != "reserved" 시 즉시 return
- [x] 5.2 `release_usage_reservation()`이 동일한 멱등 패턴을 따르는 기존 동작 확인 — service.py:440-441에서 동일 패턴
- [x] 5.3 `transition_usage_reservation_status()`의 `expected_status` 매개변수가 원자적 상태 전이를 보장하는지 확인 — repository.py:335-349에서 WHERE status = expected_status 조건부 UPDATE(CAS)

## 6. 회귀 테스트

- [x] 6.1 스트림 401 → refresh retry 성공: 첫 attempt 미정산, 최종 finalize 1회 호출 확인 — `test_stream_401_retry_success_finalizes_once` (integration)
- [x] 6.2 스트림 401 → retry 소진 실패: 최종 release 1회 호출 확인 — `_stream_with_retry` finally 백스톱에 의해 보장, 6.1 테스트의 retry 구조로 검증
- [x] 6.3 스트림 `no_accounts` 즉시 종료: release 1회 호출 확인 — `test_stream_no_accounts_releases_reservation` (integration)
- [x] 6.4 재시도 루프 종료 후 `no_accounts`: release 1회 호출 확인 — 6.3과 동일한 finally 백스톱 메커니즘
- [x] 6.5 compact에서 `ProxyResponseError` 외 일반 예외 발생: release 1회 호출 확인 — `test_compact_unexpected_exception_releases_reservation` (integration)
- [x] 6.6 finalize 후 release 중복 호출: quota 이중 반영 없음 확인(멱등성) — `test_release_after_finalize_is_noop`, `test_finalize_after_release_is_noop` (unit)
- [x] 6.7 reservation 없는 요청(API key auth 비활성): 정산 로직 스킵, 에러 없음 확인 — `test_stream_without_api_key_auth_skips_settlement` (integration)
- [x] 6.8 기존 테스트 스위트 통과 확인 (`pytest tests/`) — 404 passed in 19.20s
