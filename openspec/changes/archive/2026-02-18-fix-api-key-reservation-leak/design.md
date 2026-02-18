## Context

API key usage reservation은 2단계 정산(reserve → finalize/release) 패턴으로 구현되어 있다. 요청 시작 시 보수적 budget을 예약하고, 응답 완료 시 실제 사용량으로 finalize하거나 실패 시 release한다.

현재 정산 책임 분배:
- `_stream_once()`: attempt 단위 로직으로, `finally` 블록에서 `settle_reservation` 플래그에 따라 finalize 또는 release 수행
- `_stream_with_retry()`: 요청 단위 retry 루프로, 401 시 동일 reservation으로 재시도하지만 정산 책임을 갖지 않음
- `_compact_responses()` (API 레이어): 특정 예외(`NotImplementedError`, `ProxyResponseError`)에서만 release

이 구조에서 3가지 결함이 발생:
1. 첫 attempt의 `finally`가 reservation을 실패 정산한 뒤, retry attempt의 finalize가 no-op → quota bypass
2. `_stream_with_retry()`가 `_stream_once()`를 호출하지 않고 종료하면 정산 경로 부재 → reservation 누수
3. compact 경로에서 예외 타입 추가 시 release 누락이 재발하기 쉬운 구조 → reliability gap

## Goals / Non-Goals

**Goals:**
- Reservation 정산을 요청 단위에서 exactly-once로 보장
- 재시도 가능한 중간 상태에서 정산을 defer
- 모든 종료 경로(성공, 실패, 조기 종료, 예외)에서 reservation 정리 보장
- finalize/release 멱등성 유지

**Non-Goals:**
- Reservation 데이터 모델 또는 DB 스키마 변경
- 2단계 정산 패턴 자체의 재설계 (reserve → finalize/release 유지)
- API 응답 형태 또는 에러 코드 변경
- Rate limit 헤더 계산 로직 변경

## Decisions

### 1. 요청 단위 정산 책임 단일화

**결정**: `_stream_once()`에서 최종 정산 책임을 제거하고, `_stream_with_retry()` 레벨의 `finally`에서 exactly-once 정산을 보장한다.

**근거**: attempt 단위에서 정산하면 retry 시 이미 settled 상태의 reservation으로 재시도하게 되어 no-op finalize가 발생한다. 정산 책임을 요청 단위로 올리면 retry 여부와 무관하게 단일 지점에서 정산된다.

**구현**:
- 요청 스코프 상태 도입: `settled: bool = False`
- `_stream_with_retry()` `finally` 블록에서 `if not settled: await release_usage_reservation(...)`
- 성공 시 finalize 직후 `settled = True`

```python
settled = False
try:
    result = await self._run_stream_retry_loop(...)
    if result.success:
        await self._api_key_service.finalize_usage_reservation(...)
        settled = True
finally:
    if not settled:
        await self._api_key_service.release_usage_reservation(...)
```

**대안**: `_stream_once()`에 retry 컨텍스트를 주입 → 함수 책임이 attempt 단위를 벗어나 복잡도 증가. 정산 시점을 호출자가 관리하는 것이 더 명확.

### 2. 401 Refresh Retry 시 정산 Defer

**결정**: `_stream_once()`에서 401이 "재시도 가능"으로 판정되면 해당 attempt에서 정산하지 않는다. 재시도 신호를 명시 타입(`_RetryableStreamError`)으로 반환하고, `settle_reservation = False`를 설정한다.

**근거**: 동일 reservation을 다음 attempt에서 재사용해야 하므로 중간 attempt에서 finalize/release하면 안 된다. 상위 retry 루프가 최종 결과에 따라 1회만 정산한다.

**구현**:
- `_stream_once()`에서 401 + refresh 성공 판정 시 `settle_reservation = False` 설정 후 `_RetryableStreamError` raise
- `_stream_once()`의 `finally`에서 `settle_reservation == False`이면 정산 스킵
- Decision 1의 요청 단위 `finally`가 최종 백스톱 역할

**대안**: Reservation을 attempt마다 새로 생성 → CAS 연산 증가 + quota 누적 계산 복잡도 증가.

### 3. No-Account / 조기 종료 경로 백스톱 정산

**결정**: `_stream_with_retry()` 내에서 `selection.account is None` 즉시 반환 경로와 재시도 소진 후 `no_accounts` 종료 경로 모두 Decision 1의 `finally` 백스톱에 의해 자동으로 release된다.

**근거**: "업스트림 호출 미진입"도 정상적인 terminal failure로 분류해야 한다. 별도의 release 호출을 각 분기에 추가하면 유지보수 부담이 증가하므로, 공통 `finally` 경로로 통합한다.

**구현**:
- `_stream_with_retry()`가 어떤 경로로 종료되든 `finally` 블록에서 `if not settled` 검사를 통해 release
- `_stream_once()` 호출 전에 조기 반환하는 경우에도 `settled == False`이므로 자동 release

**대안**: 각 조기 종료 지점마다 명시적 release 호출 → 분기 누락 위험, 코드 중복.

### 4. Compact 경로 예외 타입 무관 Fail-Safe Cleanup

**결정**: API 레이어에서 `_compact_responses()` 호출 전체를 `try/except/finally`로 감싸 미정산 reservation release를 보장한다. 도메인 예외와 런타임 예외를 동일한 정산 계약으로 처리한다.

**근거**: 현재 특정 예외(`NotImplementedError`, `ProxyResponseError`)만 처리하므로 새 예외 타입이 추가될 때마다 release 누락이 재발한다. `finally` 기반 패턴으로 예외 타입에 의존하지 않는 구조를 만든다.

**구현**:
```python
settled = False
try:
    result = await context.service.compact_responses(...)
    await context.service.finalize_usage_reservation(...)
    settled = True
except ProxyResponseError:
    # 에러 응답 생성 (release는 finally에서)
    ...
finally:
    if not settled and reservation_id:
        await context.service.release_usage_reservation(...)
```

**대안**: 모든 예외 타입을 나열 → 확장 시 누락 불가피. Middleware 레벨 cleanup → 모든 라우트에 영향, 과도한 범위.

### 5. Finalize / Release 멱등성 유지

**결정**: `finalize_usage_reservation()`과 `release_usage_reservation()`은 이미 `reserved` 상태가 아닌 reservation에 대해 조기 반환(no-op)하는 멱등 동작을 유지한다. 이 계약을 명시적으로 문서화하고 테스트로 보장한다.

**근거**: Decision 1~4의 백스톱 패턴에서 finalize 후 `finally`의 release가 호출될 수 있다. 멱등성이 보장되면 이중 호출이 안전하며, 방어적 프로그래밍 비용 없이 코드를 단순하게 유지할 수 있다.

**구현**:
- 기존 동작 확인: `finalize_usage_reservation()`은 reservation status가 `reserved`가 아니면 조기 반환
- 기존 동작 확인: `release_usage_reservation()`도 동일한 패턴
- DB 상태 전이(`reserved → settling → finalized`, `reserved → released`)는 `transition_usage_reservation_status()`의 `expected_status` 매개변수로 원자적 보장
- 회귀 테스트 추가: finalize/release 중복 호출 시 quota 이중 반영 없음

**대안**: 호출 전 상태 검사를 caller에서 수행 → 모든 호출처에 중복 로직, 레이스 컨디션 가능.

## Risks / Trade-offs

- **정산 책임 이전의 코드 복잡도**: `_stream_once()`에서 `_stream_with_retry()`로 정산 책임을 옮기면 함수 간 계약이 변경됨 → 기존 정산 로직 제거와 새 `finally` 추가를 원자적으로 적용해야 함. 중간 상태에서 양쪽 모두 정산하거나 양쪽 모두 정산하지 않는 상황 방지 필요.
- **retry 루프 내 상태 추적**: `settled` 플래그가 retry 루프의 여러 iteration에 걸쳐 정확히 관리되어야 함 → 단순 boolean이므로 복잡도는 낮지만, 향후 retry 로직 변경 시 주의 필요.
- **compact 경로 에러 핸들링 변경**: `finally` 기반으로 변경하면 기존 특정 예외 처리 블록의 역할이 "에러 응답 생성"으로 한정됨 → 정산과 에러 응답 생성의 관심사 분리가 더 명확해지는 장점.
- **멱등 호출 비용**: finalize 후 `finally`에서 release가 DB 조회를 한 번 더 수행 → reservation이 이미 finalized이면 즉시 반환하므로 쿼리 1회 추가. 성공 경로에서의 미미한 오버헤드이지만, 안전성 대비 수용 가능.
