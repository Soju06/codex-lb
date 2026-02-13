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

## Risks / Trade-offs

- **Rate limit 헤더 staleness**: 캐시 TTL 동안 값이 stale할 수 있음 → usage refresh 주기와 동기화하여 실질적 영향 최소화. refresh 완료 시 즉시 invalidate.
- **Settings 캐시 일관성**: 5초 TTL 내 설정 변경이 반영되지 않을 수 있음 → 이미 auth middleware에서 동일 패턴 사용 중이므로 기존과 동일한 수준.
- **`refresh_accounts()` 반환값 변경**: 기존 호출처에 영향 → 호출처가 `LoadBalancer`와 `RefreshScheduler` 2곳뿐이므로 영향 범위 제한적.
- **subquery 방식의 `latest_by_account()`**: usage_history 테이블이 매우 클 경우 GROUP BY 성능 → `(account_id, window)` 인덱스로 커버 가능.
