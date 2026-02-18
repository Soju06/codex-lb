# Review Context: fix-api-key-reservation-leak

## 1) Latest Review Findings (2026-02-18)

- `[P1] Preserve usage reservation during 401 refresh retry`
  - 위치: `app/modules/proxy/service.py:330-338`
  - 현상: 첫 `_stream_once` 시도에서 `finally`로 예약이 실패 정산된 뒤, 같은 reservation으로 재시도하여 성공해도 `finalize/release`가 no-op이 되어 사용량이 과금되지 않음(쿼터 우회).

- `[P1] Release reservation when stream exits before settlement`
  - 위치: `app/modules/proxy/service.py:289-296`
  - 현상: 예약 생성 후 `_stream_with_retry()`가 `no_accounts`로 조기 종료되면 `_stream_once`의 정산 `finally`를 거치지 않아 reservation이 `reserved`로 남고 pre-reserved quota가 누수됨(오탐 429).

- `[P2] Add fail-safe reservation cleanup for compact exceptions`
  - 위치: `app/modules/proxy/api.py:370-377`
  - 현상: `_compact_responses()`가 `NotImplementedError`/`ProxyResponseError`에서만 release하고, 기타 예외에서 예약 정리가 누락되어 실패 요청이 quota를 점유함.

## 2) Impact Summary

- Quota bypass: 401 refresh 재시도 성공 요청이 과금 누락될 수 있음.
- False lockout: 실패/조기 종료 요청이 reservation 누수를 남겨 정상 키도 조기 `rate_limit_exceeded` 발생 가능.
- Reliability gap: 예외 타입 추가 시 예약 정리 누락이 재발하기 쉬운 구조.

## 3) Best-Practice Resolution

핵심 원칙: **reservation 정산은 요청 단위에서 exactly-once로 보장하고, 재시도 가능한 중간 상태에서는 정산을 defer한다.**

### 3.1 요청 단위 정산 책임 단일화

- `_stream_once()`는 "시도(attempt) 단위" 로직으로 제한하고, 최종 정산 책임은 `_stream_with_retry()`에 둔다.
- 요청 스코프 상태를 명시한다: `reservation_settled: bool`, `final_usage: UsageTotals | None`, `terminal_result: success|failed|aborted`.
- 요청 종료 `finally`에서 미정산이면 반드시 release한다.

```python
settled = False
try:
    result = await run_stream_retry_loop(...)
    if result.success:
        await finalize_usage_reservation(...)
        settled = True
finally:
    if not settled:
        await release_usage_reservation(...)
```

### 3.2 401 refresh retry는 동일 reservation 유지 + 정산 defer

- `_stream_once()`에서 `401`이 "재시도 가능"으로 판정되면 해당 attempt에서는 정산하지 않는다.
- 상위 retry 루프가 다음 attempt를 수행하고, 최종 성공 시 1회 finalize / 최종 실패 시 1회 release를 수행한다.
- 필요하면 retry 신호를 명시 타입으로 반환한다(예: `RetrySignal(reason="unauthorized_refresh")`).

### 3.3 no-account/조기 종료 경로에 백스톱 정산

- `selection.account is None` 즉시 반환 경로에서 release를 보장한다.
- 재시도 소진 후 `no_accounts` 종료 경로에서도 release를 보장한다.
- "업스트림 호출 미진입"도 정상적인 terminal failure로 분류해 공통 정산 경로로 보낸다.

### 3.4 compact 경로는 예외 타입 무관 fail-safe cleanup

- API 레이어에서 특정 예외만 처리하지 말고, `compact_responses()` 호출 전체를 `try/except/finally`로 감싸 미정산 reservation release를 보장한다.
- 도메인 예외(`ProxyResponseError`)와 런타임 예외(`Exception`)를 모두 동일한 정산 계약으로 처리한다.

### 3.5 finalize/release 멱등성 유지

- `finalize_usage_reservation()`/`release_usage_reservation()`는 settled 상태 재호출을 안전하게 무시해야 한다.
- DB 상태 전이(`reserved -> finalized/released`)를 원자적으로 보장해 이중 반영을 막는다.

## 4) Verification Checklist (Regression Tests)

1. 스트림 401 -> refresh retry 성공: 첫 attempt 미정산, 최종 finalize 1회.
2. 스트림 401 -> retry 소진 실패: 최종 release 1회.
3. 스트림 `no_accounts` 즉시 종료: release 1회.
4. 재시도 루프 종료 후 `no_accounts`: release 1회.
5. compact에서 `ProxyResponseError` 외 일반 예외 발생: release 1회.
6. finalize/release 중복 호출 상황: quota 이중 반영 없음.

## 5) Expected Outcome

- 401 재시도 경로의 사용량 과금 누락 제거.
- 조기 종료/예상 밖 예외 경로의 reservation 누수 제거.
- 스트림/컴팩트 전반에서 일관된 reservation lifecycle 계약 확보.
