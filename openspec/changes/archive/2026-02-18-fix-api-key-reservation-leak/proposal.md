## Why

API key usage reservation의 lifecycle에서 정산(settlement) 누락과 누수가 발생하는 3건의 결함이 코드 리뷰에서 확인되었다. 이로 인해 quota bypass(과금 누락)와 false lockout(오탐 429) 두 가지 상반된 장애가 동시에 발생 가능하다.

1. **401 refresh retry 시 quota bypass (P1)**: `_stream_once()`의 `finally` 블록이 첫 attempt에서 reservation을 실패 정산한 뒤, 동일 reservation으로 재시도하여 성공해도 `finalize`가 no-op이 되어 사용량이 과금되지 않음.
2. **조기 종료 시 reservation 누수 (P1)**: 예약 생성 후 `_stream_with_retry()`가 `no_accounts`로 즉시 종료되면 `_stream_once`의 정산 `finally`를 거치지 않아 reservation이 `reserved` 상태로 영구 잔류. pre-reserved quota가 누수되어 정상 키도 조기 `rate_limit_exceeded` 발생.
3. **compact 예외 경로 정리 누락 (P2)**: `_compact_responses()`가 `NotImplementedError`/`ProxyResponseError`에서만 release하고, 기타 예외에서 reservation 정리가 누락되어 실패 요청이 quota를 점유.

공통 원인: reservation 정산 책임이 attempt 단위(`_stream_once`)와 요청 단위(`_stream_with_retry`, API 레이어) 사이에 분산되어 있고, 모든 종료 경로에 대한 백스톱이 없음.

## What Changes

### Reservation Lifecycle 정산 일관성

- `_stream_once()`를 "attempt 단위" 로직으로 제한하고, 최종 정산 책임을 `_stream_with_retry()` 요청 단위 `finally`로 이전
- 401 refresh retry 시 동일 reservation 유지 + 중간 attempt에서 정산 defer — 최종 성공 시 1회 finalize, 최종 실패 시 1회 release
- `no_accounts` 즉시 반환 및 재시도 소진 종료 경로에 백스톱 release 추가
- compact 경로에서 예외 타입 무관 `try/finally` 기반 fail-safe cleanup 적용
- `finalize_usage_reservation()` / `release_usage_reservation()` 멱등성 유지 확인 및 문서화

## Capabilities

### New Capabilities

없음 — 내부 reservation lifecycle 정합성 수정으로 새로운 capability를 도입하지 않음.

### Modified Capabilities

없음 — 외부 API 계약 변경 없음. reservation 정산 동작이 내부적으로 정확해지는 것이므로 기존 동작의 계약 위반을 수정하는 것.

## Impact

- **코드**: `app/modules/proxy/service.py` (`_stream_with_retry`, `_stream_once`), `app/modules/proxy/api.py` (`_compact_responses`)
- **API**: 응답 형태 변경 없음. 정산 정확성이 향상되어 rate limit 헤더와 429 응답이 더 정확해짐.
- **DB**: 스키마 변경 없음. `api_key_usage_reservations` 테이블의 상태 전이(`reserved → finalized/released`)가 모든 경로에서 보장됨.
- **보안**: quota bypass 취약점(401 재시도 과금 누락) 수정.
- **테스트**: 401 retry 정산, 조기 종료 release, compact 예외 release, finalize/release 멱등성에 대한 회귀 테스트 추가.
