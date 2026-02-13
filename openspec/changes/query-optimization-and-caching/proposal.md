## Why

Proxy 요청 경로에서 upstream API 호출 전 불필요한 DB 세션과 중복 쿼리가 누적되어 best-case에서도 상당한 오버헤드가 발생한다. `rate_limit_headers()`가 매 요청마다 6개 쿼리를 실행하고, `latest_by_account()`는 전체 usage_history를 Python으로 필터링하며, settings 조회가 캐시 없이 별도 세션을 연다. 대시보드 API도 동일한 패턴의 비효율을 공유한다.

## What Changes

- `rate_limit_headers()` 결과를 TTL 캐시로 전환하여 매 요청마다 6개 쿼리 실행을 제거
- `_stream_with_retry()` / `compact_responses()` 내 settings 조회를 기존 `SettingsCache` 활용으로 전환
- `LoadBalancer.select_account()`에서 refresh 스킵 시 `latest_by_account()` 재호출 제거
- `latest_by_account()` 쿼리를 DB 레벨 최적화 (전체 스캔 후 Python dedup → SQL window function / DISTINCT)
- 대시보드 `request_logs` list + count 이중 쿼리를 window function 단일 쿼리로 통합
- `request_logs/options` 3개 DISTINCT 쿼리를 단일 쿼리로 통합

## Capabilities

### New Capabilities

없음 — 내부 구현 최적화로 새로운 capability를 도입하지 않음.

### Modified Capabilities

없음 — 외부 동작 및 API 계약이 변경되지 않는 순수 성능 최적화.

## Impact

- **코드**: `app/modules/proxy/service.py`, `app/modules/proxy/load_balancer.py`, `app/modules/usage/repository.py`, `app/modules/request_logs/repository.py`, `app/modules/request_logs/service.py`
- **API**: 응답 형태 변경 없음. rate limit 헤더 값이 캐시 TTL 내에서 약간 stale할 수 있으나 기능적 영향 없음.
- **DB**: `usage_history` 테이블에 `(account_id, window, recorded_at DESC)` 복합 인덱스 추가 권장
- **테스트**: 기존 테스트의 계약 변경 없음. 캐시 동작에 대한 단위 테스트 추가.
