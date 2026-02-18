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

## 7. Refreshed 플래그 정확성 수정 (P2)

- [x] 7.1 `_refresh_account()` 반환 타입을 `AccountRefreshResult` dataclass(`usage_written: bool`)로 변경 — payload/rate_limit 없이 종료 또는 API 오류 시 `usage_written=False` 반환
- [x] 7.2 `refresh_accounts()`에서 `refreshed = refreshed or result.usage_written`으로 집계 기준 변경 — 예외 없이 종료돼도 `usage_written=False`면 `refreshed` 유지
- [x] 7.3 쓰기 성공 판정: `usage_repo.add_entry()` 호출 결과가 실제 row 생성일 때만 `usage_written=True`, primary/secondary 중 하나라도 row 생성 시 `True`
- [x] 7.4 `LoadBalancer.select_account()` 기존 분기 유지 확인 — `refreshed=False`면 `latest_primary` 재사용, `True`일 때만 재조회
- [x] 7.5 회귀 테스트: `_refresh_account()`가 payload/rate_limit 없이 종료 → `refresh_accounts()` `False` 반환
- [x] 7.6 회귀 테스트: Usage API 오류(401 재시도 실패 포함) → `False` 반환
- [x] 7.7 회귀 테스트: primary 또는 secondary row 1건 이상 생성 → `True` 반환
- [x] 7.8 회귀 테스트: 계정 여러 개 중 일부만 write 성공 → 전체 결과 `True`
- [x] 7.9 회귀 테스트: `LoadBalancer.select_account()`에서 `refreshed=False`일 때 `latest_by_account()` 재호출 없음

## 8. API Key Quota 원자적 검증 (P1)

- [x] 8.1 Repository에 `try_reserve_usage(limit_id, delta, expected_reset_at) -> ReservationResult` 메서드 추가 — `UPDATE api_key_limits SET current_value = current_value + :delta WHERE id = :limit_id AND reset_at = :expected_reset_at AND current_value + :delta <= max_value RETURNING ...` CAS 패턴
- [x] 8.2 서비스에서 `enforce_limits_for_request()` 구현 — 적용 대상 limit(모델 필터 포함) 모두에 `try_reserve_usage()` 실행, 하나라도 실패 시 전체 롤백 후 429
- [x] 8.3 2단계 정산 구조: 요청 시작 시 보수적 budget 예약(reserve), 응답 수신 시 실제 usage로 정산(finalize) + 초과 예약분 환급(release), upstream 실패/취소 시 예약 전액 해제
- [x] 8.4 멱등성/중복 반영 방지: 요청별 `usage_reservation_id`로 finalize 멱등 처리, unique key + upsert 전략
- [x] 8.5 회귀 테스트: 한계치 직전 key에 병렬 요청 N개 전송 시 허용 건수가 quota 초과하지 않음
- [x] 8.6 회귀 테스트: 예약 성공 후 upstream 실패 시 누적치 원복됨
- [x] 8.7 회귀 테스트: finalize 중복 호출 시 한 번만 반영됨(멱등성)
- [x] 8.8 회귀 테스트: 모델 필터 limit + 글로벌 limit 동시 원자 조건 보장

## 9. TOTP Disable Step-Up 강제 (P1)

- [x] 9.1 `_require_totp_verified_session(session_id)` 헬퍼 도입 — `password_verified && totp_verified` 강제
- [x] 9.2 `disable_totp()`에서 해당 헬퍼를 통과하지 못하면 즉시 거절
- [x] 9.3 코드 재검증 경로 유지 시 `verify_totp_code(..., last_verified_step=settings.totp_last_verified_step)` 사용 + `try_advance_totp_last_verified_step()`로 replay 방지
- [x] 9.4 API/세션 계약 단일화: `disable_totp()`는 `tv=true` 세션 요구 (또는 `tv=true` + `code` 보수적 모델)
- [x] 9.5 회귀 테스트: `password_verified=True`, `totp_verified=False` 세션으로 disable 요청 시 거절
- [x] 9.6 회귀 테스트: `totp_verified=True` 세션으로 disable 성공
- [x] 9.7 회귀 테스트: 동일 TOTP step 재사용 시 disable 실패(replay 차단)
- [x] 9.8 회귀 테스트: TOTP 미설정 상태에서 disable 요청 시 `TotpNotConfiguredError` 유지

## 10. Stale 필터 선택값 가시성 보장 (P2)

- [x] 10.1 `MultiSelectFilter` 렌더링 집합을 `options ∪ selectedValues` 합집합으로 변경 — `options`에 없는 선택값에 `isStale=true` 메타 부착, 구분 배지 표시
- [x] 10.2 stale 항목에 체크 해제 + `x` 버튼(칩 제거) 또는 "stale 값만 정리" 액션 제공 — global reset 없이 문제 값 즉시 제거
- [x] 10.3 필터 활성 상태의 단일 SSOT를 `selectedValues`(또는 URL query state)로 유지 — `options`는 추천/탐색용 데이터로만 사용
- [x] 10.4 회귀 테스트: 선택된 값이 최신 `options`에서 사라져도 UI에 표시되고 개별 해제 가능
- [x] 10.5 회귀 테스트: stale 값 해제 시 요청 파라미터에서 해당 값 제거, 결과 즉시 반영
- [x] 10.6 회귀 테스트: stale + normal 값 혼재 시 개별 토글 동작 간섭 없음

## 11. Status Facet Self-Filtering 방지 (P2)

- [x] 11.1 `/api/request-logs/options` 호출 payload에서 `filters.statuses`를 제외하여 status self-filter 제거 — `listFilters`와 분리 구성
- [x] 11.2 백엔드 방어 로직: status 옵션 계산 시 서버에서도 `statuses` 조건 무시 — API 레벨에서 "status facet은 self-filter 비적용" 계약 고정
- [x] 11.3 훅/서비스에서 `listFilters`(결과 조회용)와 `facetFilters`(옵션 계산용)를 타입으로 분리
- [x] 11.4 회귀 테스트: 상태 1개 선택 후 options 재조회 시 다른 상태 항목 계속 노출
- [x] 11.5 회귀 테스트: 상태 외 필터(기간/모델/계정)는 options 축소에 정상 반영
- [x] 11.6 회귀 테스트: status self-filter를 실수로 전송해도 서버 응답 동일(무시됨)
- [x] 11.7 회귀 테스트: 상태 다중 선택(추가/해제) 흐름 유지
