## Why

Proxy 요청 경로에서 upstream API 호출 전 불필요한 DB 세션과 중복 쿼리가 누적되어 best-case에서도 상당한 오버헤드가 발생한다. `rate_limit_headers()`가 매 요청마다 6개 쿼리를 실행하고, `latest_by_account()`는 전체 usage_history를 Python으로 필터링하며, settings 조회가 캐시 없이 별도 세션을 연다. 대시보드 API도 동일한 패턴의 비효율을 공유한다.

코드 리뷰에서 추가로 발견된 정확성·보안·UX 문제:

1. **Refreshed 플래그 부정확 (P2)**: `refresh_accounts()`가 `_refresh_account()` 호출 성공 여부만으로 `refreshed=True`를 설정하여, 실제 usage row 추가 없이도 `latest_by_account()` 재조회가 발생 — 최적화 목적이 오류 트래픽 구간에서 무력화됨
2. **API Key Quota 원자성 부재 (P1)**: `validate_key()`가 read-then-act으로 검사하고 실제 누적은 `record_usage()`에서 사후 증가하여, 병렬 요청 시 quota를 크게 초과할 수 있음
3. **TOTP Disable Step-Up 미강제 (P1)**: `disable_totp()`가 `password_verified=True`만 확인하고 `totp_verified=True`를 요구하지 않아, 비밀번호 유출 시 2FA 해제까지 가능
4. **Stale 필터 선택값 비노출 (P2)**: `MultiSelectFilter`가 `options` 기준으로만 렌더링하여, 서버 옵션에서 사라진 기존 선택값을 해제할 수 없음
5. **Status Facet Self-Filtering (P2)**: `/api/request-logs/options` 호출에 `filters.statuses`를 함께 전송하여, 상태 1개 선택 후 옵션이 해당 subset으로 수축되어 다중 선택 불가

## What Changes

### 기존 (Query Optimization)

- `rate_limit_headers()` 결과를 TTL 캐시로 전환하여 매 요청마다 6개 쿼리 실행을 제거
- `_stream_with_retry()` / `compact_responses()` 내 settings 조회를 기존 `SettingsCache` 활용으로 전환
- `LoadBalancer.select_account()`에서 refresh 스킵 시 `latest_by_account()` 재호출 제거
- `latest_by_account()` 쿼리를 DB 레벨 최적화 (전체 스캔 후 Python dedup → SQL window function / DISTINCT)
- 대시보드 `request_logs` list + count 이중 쿼리를 window function 단일 쿼리로 통합
- `request_logs/options` 3개 DISTINCT 쿼리를 단일 쿼리로 통합

### 추가 (Review Findings)

- `_refresh_account()` 반환 타입을 `AccountRefreshResult` dataclass로 변경하여, `usage_written: bool`로 실제 데이터 변경 여부를 명시 — `refresh_accounts()`는 `usage_written=True`인 경우에만 `refreshed=True` 집계
- API key quota 검증 + 차감(또는 예약)을 단일 원자 연산으로 처리 — `UPDATE ... WHERE current_value + :delta <= max_value RETURNING` CAS 패턴 적용, 2단계 정산(reserve → finalize/release) 구조
- `disable_totp()`에 `_require_totp_verified_session()` 강제 — `password_verified && totp_verified` 완전 인증 세션 요구, replay-safe 규칙 유지
- `MultiSelectFilter` 렌더링 집합을 `options ∪ selectedValues`로 고정 — stale 항목에 구분 배지 표시, 개별 해제 가능
- `/api/request-logs/options` 호출에서 `filters.statuses` 제외 — status facet self-filter 방지, 백엔드에도 방어 로직 추가

## Capabilities

### New Capabilities

없음 — 내부 구현 최적화 및 정확성/보안 수정으로 새로운 capability를 도입하지 않음.

### Modified Capabilities

없음 — 외부 동작 및 API 계약이 변경되지 않는 순수 성능 최적화 및 내부 정확성·보안 수정.

## Impact

- **코드**: `app/modules/proxy/service.py`, `app/modules/proxy/load_balancer.py`, `app/modules/usage/repository.py`, `app/modules/usage/updater.py`, `app/modules/request_logs/repository.py`, `app/modules/request_logs/service.py`, `app/modules/api_keys/service.py`, `app/modules/api_keys/repository.py`, `app/modules/dashboard_auth/service.py`, `frontend/src/features/dashboard/components/filters/multi-select-filter.tsx`, `frontend/src/features/dashboard/hooks/use-request-logs.ts`
- **API**: 응답 형태 변경 없음. rate limit 헤더 값이 캐시 TTL 내에서 약간 stale할 수 있으나 기능적 영향 없음.
- **DB**: `usage_history` 테이블에 `(account_id, window, recorded_at DESC)` 복합 인덱스 추가 권장. `api_key_limits` 테이블에 조건부 UPDATE(CAS) 쿼리 도입.
- **보안**: API key quota 동시성 취약점 수정, TOTP disable 경로 step-up 강제
- **테스트**: 기존 테스트의 계약 변경 없음. 캐시 동작, refreshed 정확성, quota 원자성, TOTP 세션 검증, 필터 UX에 대한 테스트 추가.
