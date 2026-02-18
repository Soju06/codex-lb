## Context

프록시 요청의 critical path에서 upstream API 호출 전에 다수의 DB 세션과 중복 쿼리가 실행된다.

현재 best-case per-request 오버헤드:
- DB 세션 5개 (auth middleware, rate_limit_headers, settings, select_account, ensure_fresh)
- `latest_by_account()` 쿼리가 전체 usage_history를 로드 후 Python dedup
- `rate_limit_headers()`가 매 요청 6개 쿼리 실행 (결과는 usage refresh 주기 내 동일)
- settings 조회가 `SettingsCache` 미사용으로 별도 세션

대시보드도 동일한 `latest_by_account()` 비효율과 request_logs의 이중 쿼리를 공유한다.

## Goals / Non-Goals

**Goals:**
- 프록시 요청당 DB 세션 수를 5개 → 2개로 감소
- `rate_limit_headers()` 쿼리를 캐시로 대체하여 매 요청 6개 쿼리 제거
- `latest_by_account()` 쿼리 효율화로 전체 경로(proxy + dashboard) 개선
- 대시보드 request_logs 쿼리 최적화

**Non-Goals:**
- API 응답 형태 또는 계약 변경
- Connection pooling 재설계 (현재 SQLAlchemy async pool로 충분)
- 프론트엔드 변경
- usage refresh 스케줄러 아키텍처 변경

## Decisions

### 1. `rate_limit_headers()` TTL 캐시

**결정**: `RateLimitHeadersCache` 클래스를 `SettingsCache`와 동일한 패턴으로 구현. TTL은 `usage_refresh_interval_seconds`와 동기화.

**대안**: 매 요청마다 계산하되 쿼리만 최적화 → 세션 자체를 줄이지 못함.

**구현**:
- `app/modules/proxy/rate_limit_cache.py` 신규 모듈
- `SettingsCache`와 동일한 `asyncio.Lock` + `time.monotonic()` 패턴
- usage refresh 스케줄러가 refresh 완료 시 캐시를 invalidate하여 freshness 보장
- `ProxyService.rate_limit_headers()`는 캐시에서 읽기만 수행

### 2. Settings 조회를 `SettingsCache` 활용

**결정**: `_stream_with_retry()`와 `compact_responses()`에서 `SettingsCache.get()`을 직접 사용하여 별도 DB 세션 제거.

**근거**: `SettingsCache`는 이미 auth middleware에서 사용 중이며 TTL 5초로 충분히 fresh. `ProxyService`가 `repo_factory`로 세션을 열어 settings를 읽는 것은 불필요.

**구현**:
- `ProxyService._stream_with_retry()` / `compact_responses()` 내 `async with self._repo_factory() as repos: settings = await repos.settings.get_or_create()` 블록을 `settings = await get_settings_cache().get()`으로 대체

### 3. `select_account()` 중복 쿼리 제거

**결정**: `refresh_accounts()` 반환 후 실제 refresh가 발생했는지 여부를 반환값으로 알리고, 미발생 시 기존 `latest_primary` 재사용.

**구현**:
- `UsageUpdater.refresh_accounts()`가 `bool` (갱신 발생 여부) 반환
- `LoadBalancer.select_account()`에서 `refreshed == False`이면 line 55의 결과를 그대로 사용

### 4. `latest_by_account()` SQL 최적화

**결정**: 전체 row 로드 후 Python dedup 대신, SQLAlchemy subquery로 계정당 최신 1건만 조회.

**대안**: `DISTINCT ON` → SQLite 미지원. Window function `ROW_NUMBER()` → 범용적이지만 복잡.

**구현**: 서브쿼리 방식 채택 (SQLite + PostgreSQL 모두 호환):
```python
subq = (
    select(
        UsageHistory.account_id,
        func.max(UsageHistory.id).label("max_id"),
    )
    .where(conditions)
    .group_by(UsageHistory.account_id)
    .subquery()
)
stmt = select(UsageHistory).join(
    subq, UsageHistory.id == subq.c.max_id
)
```

### 5. Request logs list + count 통합

**결정**: `list_recent()` 내에서 window function `COUNT(*) OVER()`를 사용하여 단일 쿼리로 rows + total 반환.

**구현**:
- `list_recent()`가 `tuple[list[RequestLog], int]` 반환 (rows, total_count)
- `RequestLogsService.list_recent()`에서 `count_recent()` 호출 제거

### 6. Request logs filter options 통합

**결정**: 3개 DISTINCT 쿼리를 유지하되 순차 실행이 아닌 의미적으로는 변경 없음. 이 쿼리들은 대시보드 UX에서 드물게 호출되므로 최적화 우선순위 낮음.

**근거**: 3개 쿼리를 단일로 통합하면 쿼리 복잡도가 크게 증가하고, 호출 빈도가 낮아 실질적 효과 미미.

### 7. `refresh_accounts()` refreshed 플래그 정확성

**결정**: `_refresh_account()` 반환 타입을 `AccountRefreshResult(usage_written: bool)`로 변경하고, `refresh_accounts()`는 `usage_written=True`인 경우에만 `refreshed=True`로 집계.

**근거**: 현재 로직은 "refresh 시도"와 "usage 데이터 변경"을 동일하게 취급하여, payload 없음/API 오류/rate limit 정보 없음 등으로 DB 쓰기 없이 종료되는 경우에도 `refreshed=True`가 설정됨. 이로 인해 쿼리 최적화 목적(불필요한 `latest_by_account()` 재조회 억제)이 오류 트래픽 구간에서 무력화됨.

**구현**:
- `_refresh_account()` → `AccountRefreshResult` dataclass 반환
- `usage_repo.add_entry()` 호출 결과가 실제 row 생성일 때만 `usage_written=True`
- primary/secondary 중 하나라도 row 생성 시 `True`
- `refresh_accounts()`에서 `refreshed = refreshed or result.usage_written`

### 8. API Key Quota 원자적 검증

**결정**: 승인(allow)과 quota 차감/예약을 같은 원자 연산으로 처리. Repository에 `try_reserve_usage()` CAS 메서드를 추가하고, 서비스는 이 결과로만 허용/거절을 결정.

**근거**: 현재 `validate_key()`는 read-then-act(`current_value >= max_value`)로 검사하고, 실제 누적은 `record_usage()`에서 사후 증가. 병렬 요청 시 동일한 stale counter를 통과해 quota를 크게 초과할 수 있음.

**구현**:
- CAS SQL: `UPDATE api_key_limits SET current_value = current_value + :delta WHERE id = :limit_id AND reset_at = :expected_reset_at AND current_value + :delta <= max_value RETURNING ...`
- 2단계 정산: 요청 시작 시 보수적 budget 예약(reserve) → 응답 수신 시 실제 usage로 정산(finalize) + 초과 예약분 환급(release) → upstream 실패/취소 시 예약 전액 해제
- 요청별 `usage_reservation_id`로 finalize 멱등 처리

**대안**: 낙관적 잠금(optimistic lock) + 재시도 → 경합이 높을 때 재시도 비용 증가. Redis 기반 atomic counter → 인프라 복잡도 증가.

### 9. TOTP Disable Step-Up 강제

**결정**: `disable_totp()`에 `_require_totp_verified_session()` 헬퍼를 도입하여 `password_verified && totp_verified` 완전 인증 세션을 요구.

**근거**: 현재 `disable_totp()`는 `_require_active_password_session()`만 호출하여 `password_verified=True`만 확인. 비밀번호 유출 시 TOTP 검증 없이 2FA 해제가 가능해짐.

**구현**:
- `_require_totp_verified_session(session_id)` → `password_verified && totp_verified` 강제
- 코드 재검증 시 `verify_totp_code(..., last_verified_step=settings.totp_last_verified_step)` + `try_advance_totp_last_verified_step()` replay 방지
- 운영 모델: `tv=true` 세션만 요구 (단순) 또는 `tv=true` + `code` (보수적)

### 10. Stale 필터 선택값 가시성

**결정**: `MultiSelectFilter` 렌더링 집합을 `options ∪ selectedValues` 합집합으로 구성. `options`에 없는 선택값은 `isStale=true` 메타를 부착하여 구분.

**근거**: 현재 필터 상태(source of truth)는 `selectedValues`이지만 UI 렌더링 집합은 `options`로만 제한되어, 서버 옵션에서 사라진 기존 선택값을 해제할 수 없음.

**구현**:
- `mergeFacetOptions(options, selectedValues)` 유틸리티로 합집합 구성
- stale 항목에 구분 배지 + 체크 해제/`x` 버튼 제공
- 필터 활성 상태 SSOT는 `selectedValues`(또는 URL query state)로 유지

### 11. Status Facet Self-Filtering 방지

**결정**: `/api/request-logs/options` 호출 시 `filters.statuses`를 제외하여 status facet이 자기 자신으로 축소되지 않도록 함.

**근거**: 옵션 조회 쿼리가 목록 조회와 동일 필터를 재사용하면서, 백엔드가 status 조건을 적용해 status 옵션을 계산 → facet이 "한 번 좁히면 복구 어려운 단일 선택"처럼 동작.

**구현**:
- `listFilters`(실제 결과 조회용)와 `facetFilters`(옵션 계산용)를 타입으로 분리
- status 옵션 계산 요청에는 `statuses` 제외 필터만 전달
- 백엔드 방어: status 옵션 계산 시 서버에서도 `statuses` 조건 무시

## Risks / Trade-offs

- **Rate limit 헤더 staleness**: 캐시 TTL 동안 값이 stale할 수 있음 → usage refresh 주기와 동기화하여 실질적 영향 최소화. refresh 완료 시 즉시 invalidate.
- **Settings 캐시 일관성**: 5초 TTL 내 설정 변경이 반영되지 않을 수 있음 → 이미 auth middleware에서 동일 패턴 사용 중이므로 기존과 동일한 수준.
- **`refresh_accounts()` 반환값 변경**: 기존 호출처에 영향 → 호출처가 `LoadBalancer`와 `RefreshScheduler` 2곳뿐이므로 영향 범위 제한적.
- **subquery 방식의 `latest_by_account()`**: usage_history 테이블이 매우 클 경우 GROUP BY 성능 → `(account_id, window)` 인덱스로 커버 가능.
- **CAS 기반 quota 예약**: UPDATE 실패 시 즉시 429 반환 → 정상적인 경합 상황에서도 false-reject 가능성. 단, quota 초과 방지가 더 중요한 요구사항이므로 수용.
- **2단계 정산 복잡도**: reserve → finalize/release 패턴은 코드 복잡도 증가 → upstream 실패 시 자동 해제 + 멱등 finalize로 안전장치 확보.
- **TOTP step-up 추가**: disable_totp() UX 단계가 늘어남 → 보안 민감 작업이므로 수용. 기존 verify_totp()와 동일한 anti-replay contract 재사용으로 구현 복잡도 최소화.
