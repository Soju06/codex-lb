## 1. latest_by_account() SQL 최적화

- [x] 1.1 `UsageRepository.latest_by_account()`를 서브쿼리 방식으로 변경 — `GROUP BY account_id` + `MAX(id)` 서브쿼리로 계정당 최신 1건만 조회. 반환 타입 `dict[str, UsageHistory]` 유지.
- [x] 1.2 기존 `latest_by_account()` 호출처(proxy, dashboard, accounts, usage) 동작 확인 — 반환값 형태가 동일하므로 호출처 변경 불필요하지만 테스트로 검증.

## 2. Rate limit headers 캐시

- [x] 2.1 `app/modules/proxy/rate_limit_cache.py` 신규 모듈 생성 — `RateLimitHeadersCache` 클래스 구현 (`asyncio.Lock` + `time.monotonic()` TTL 패턴, `SettingsCache`와 동일 구조). TTL은 `settings.usage_refresh_interval_seconds`.
- [x] 2.2 `ProxyService.rate_limit_headers()`를 `RateLimitHeadersCache.get()` 위임으로 변경 — 캐시 미스 시에만 기존 DB 쿼리 로직 실행.
- [x] 2.3 `UsageRefreshScheduler._refresh_once()` 완료 후 `RateLimitHeadersCache.invalidate()` 호출 추가.
- [x] 2.4 `RateLimitHeadersCache` 단위 테스트 추가 — TTL 내 캐시 히트, TTL 만료 후 재계산, invalidate 후 재계산 시나리오.

## 3. Settings 캐시 활용

- [x] 3.1 `ProxyService._stream_with_retry()` 내 settings 조회를 `get_settings_cache().get()`으로 변경 — `async with self._repo_factory() as repos: settings = await repos.settings.get_or_create()` 블록 제거.
- [x] 3.2 `ProxyService.compact_responses()` 내 동일 패턴 적용.
- [x] 3.3 `ProxyRepoFactory` / `ProxyRepositories`에서 `settings` 필드가 더 이상 프록시 요청 경로에서 사용되지 않음을 확인. 다른 호출처(dashboard 등)가 사용하면 유지, 아니면 제거.

## 4. select_account() 중복 쿼리 제거

- [x] 4.1 `UsageUpdater.refresh_accounts()` 반환 타입을 `bool`로 변경 — 하나 이상의 계정이 실제 갱신되었으면 `True`, 전부 스킵이면 `False`.
- [x] 4.2 `LoadBalancer.select_account()`에서 `refreshed` 반환값에 따라 분기 — `False`이면 line 55의 `latest_primary` 재사용, `True`이면 재쿼리.
- [x] 4.3 `UsageRefreshScheduler._refresh_once()`의 `refresh_accounts()` 호출부 반환값 무시 처리 (기존 동작 유지).

## 5. Request logs 쿼리 최적화

- [x] 5.1 `RequestLogsRepository.list_recent()`에 `COUNT(*) OVER()` window function 추가 — `tuple[list[RequestLog], int]` 반환으로 변경.
- [x] 5.2 `RequestLogsService.list_recent()`에서 `count_recent()` 호출 제거 — `list_recent()` 반환값에서 total 추출.
- [x] 5.3 `RequestLogsRepository.count_recent()` 메서드를 다른 호출처가 없으면 제거.

## 6. 테스트 및 검증

- [x] 6.1 기존 테스트 스위트 통과 확인 (`pytest tests/`).
- [x] 6.2 `latest_by_account()` 최적화에 대한 단위 테스트 — 계정당 최신 1건만 반환, window 필터 동작 확인.
- [x] 6.3 request logs list + count 통합에 대한 단위 테스트 — 페이지네이션 + total count 정확성 확인.
