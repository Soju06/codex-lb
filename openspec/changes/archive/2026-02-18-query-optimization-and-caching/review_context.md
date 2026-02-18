# Review Context: query-optimization-and-caching

## 1) Review 요약

- 날짜: 2026-02-18
- 코멘트: `[P2] Set refreshed flag only when usage data actually changes`
- 위치: `app/modules/usage/updater.py:71-72`
- 현상: `refresh_accounts()`가 `_refresh_account()` 호출 성공 여부만으로 `refreshed=True`를 설정하여, 실제 usage row가 추가되지 않은 경우에도 `LoadBalancer.select_account()`에서 `latest_by_account()` 재조회가 발생함.

## 2) 문제 원인

현재 로직은 "refresh 시도"와 "usage 데이터 변경"을 동일하게 취급한다.

- `_refresh_account()`는 아래 케이스에서 DB 쓰기 없이 조기 반환 가능
- Usage API 일시 오류/401 재시도 실패
- payload 없음
- rate limit 정보 없음

하지만 `refresh_accounts()`는 위 케이스에서도 `_refresh_account()`가 예외만 없으면 `refreshed=True`를 설정한다.  
결과적으로 query-optimization 목표였던 불필요한 `latest_by_account()` 재조회 제거가 오류 트래픽 구간에서 무력화된다.

## 3) Best Practice 해결안

핵심 원칙: `refreshed`는 "usage_history가 실제로 변경되었는지"만 표현해야 한다.

### 3.1 반환 계약을 명확히 분리

- `_refresh_account()` 반환 타입을 `None`에서 명시적 결과 타입으로 변경
- 예: `AccountRefreshResult` dataclass

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class AccountRefreshResult:
    usage_written: bool
```

권장 시그니처:

```python
async def _refresh_account(...) -> AccountRefreshResult: ...
```

### 3.2 refreshed 집계 기준을 데이터 변경으로 제한

- `refresh_accounts()`에서 `refreshed = refreshed or result.usage_written`
- 예외 없이 종료되더라도 `usage_written=False`면 `refreshed`는 유지

### 3.3 쓰기 성공 판정 규칙 단일화

- `usage_repo.add_entry(...)` 호출 결과가 실제 row 생성일 때만 `usage_written=True`
- primary/secondary 중 하나라도 row가 생성되면 `True`

### 3.4 LoadBalancer 연계 의미 유지

- `LoadBalancer.select_account()`는 기존 분기 유지
- `refreshed=False`면 기존 `latest_primary` 재사용
- `refreshed=True`일 때만 `latest_by_account()` 재조회

## 4) 테스트 보강 (회귀 방지)

최소 검증 시나리오:

1. `_refresh_account()`가 payload/rate_limit 없이 종료 → `refresh_accounts()`는 `False`
2. Usage API 오류(401 재시도 실패 포함) → `False`
3. primary 또는 secondary row 1건 이상 생성 → `True`
4. 계정 여러 개 중 일부만 write 성공 → 전체 결과 `True`
5. `LoadBalancer.select_account()`에서 `refreshed=False`일 때 `latest_by_account()` 재호출 없음

## 5) 기대 효과

- 오류 트래픽 구간에서도 최적화 목적(불필요한 재조회 억제) 유지
- `refresh` 의미가 "시도"가 아닌 "데이터 변경"으로 고정되어 유지보수성 향상
- 함수 계약이 타입으로 문서화되어 추후 회귀 가능성 감소

## 6) 추가 Review 요약 (Security P1)

- 날짜: 2026-02-18
- 코멘트 1: `[P1] Enforce API key limit checks atomically before request handling`
- 위치: `app/modules/api_keys/service.py:240-243`
- 현상: `validate_key()`는 read-then-act(`current_value >= max_value`)로 검사하고, 실제 누적은 `record_usage()`에서 사후 증가한다. 병렬 요청 시 동일한 stale counter를 통과해 quota를 크게 초과할 수 있다.
- 코멘트 2: `[P1] Require fully verified session for TOTP disable flow`
- 위치: `app/modules/dashboard_auth/service.py:283-289`
- 현상: `disable_totp()`가 password-authenticated 세션만 요구하고 `tv=true`(TOTP 검증 완료 세션)를 강제하지 않는다. 같은 윈도우의 코드 재사용/탈취 시 2FA 해제까지 가능해진다.

## 7) P1-1 문제 원인 (API Key Quota 원자성 부재)

- 인증 단계(`validate_key`)와 누적 단계(`record_usage`)가 분리되어 있고, 두 단계 사이에 upstream 호출 시간이 존재한다.
- limit 체크가 트랜잭션 경계 밖에서 수행되어 동시성 제어(행 잠금/CAS)가 없다.
- 결과적으로 "검증 시점에는 여유가 있었던" 여러 요청이 동시에 승인되어, 누적 반영 시 초과가 한 번에 발생한다.

## 8) P1-1 Best Practice 해결안

핵심 원칙: 승인(allow)과 quota 차감/예약(consumption or reservation)을 같은 원자 연산으로 처리한다.

### 8.1 검증 + 차감(또는 예약) 단일 원자 연산

- Repository에 `try_reserve_usage(...) -> ReservationResult` 계열 메서드를 추가하고, 서비스는 이 결과로만 허용/거절을 결정한다.
- SQL은 `current_value + :delta <= max_value` 조건을 포함한 조건부 `UPDATE ... RETURNING`(CAS)로 처리한다.
- 적용 대상 limit(모델 필터 포함) 중 하나라도 실패하면 전체 트랜잭션 롤백 후 `ApiKeyRateLimitExceededError` 반환.

예시 패턴:

```sql
UPDATE api_key_limits
SET current_value = current_value + :delta
WHERE id = :limit_id
  AND reset_at = :expected_reset_at
  AND current_value + :delta <= max_value
RETURNING id, current_value, max_value, reset_at;
```

### 8.2 사후 usage 반영 구조와 충돌하지 않게 2단계 정산

- 요청 시작 시: 보수적 budget 예약(estimate) 수행.
- 응답 수신 시: 실제 usage로 정산(finalize)하고 초과 예약분은 환급(release).
- upstream 실패/취소 시: 예약 전액 해제.
- 이렇게 하면 "요청 처리 전 원자적 gate"를 만족하면서도 실제 토큰 기반 과금 정확도를 유지할 수 있다.

### 8.3 멱등성/중복 반영 방지

- 요청별 `usage_reservation_id`(또는 request_id)로 finalize를 멱등 처리.
- 재시도/네트워크 중복 전송 시 중복 누적이 발생하지 않도록 unique key + upsert 전략 적용.

### 8.4 테스트 보강 (동시성 회귀 방지)

1. 한계치 직전 key에 병렬 요청 N개 전송 시 허용 건수가 quota를 초과하지 않음
2. 예약 성공 후 upstream 실패 시 누적치 원복됨
3. finalize 중복 호출 시 한 번만 반영됨(멱등성)
4. 모델 필터 limit + 글로벌 limit이 동시에 있을 때 둘 다 원자 조건으로 보장됨

## 9) P1-2 문제 원인 (TOTP Disable Step-Up 미강제)

- `disable_totp()`가 `_require_active_password_session()`만 호출하여 `password_verified=True`만 확인한다.
- `verify_totp()` 경로에서 보장되는 replay 방지(`last_verified_step` advance)와 세션 단계 상승(`tv=true`)을 disable 단계에서 재사용하지 않는다.
- 결과적으로 비밀번호가 유출된 시나리오에서 2차 인증 강도가 의도보다 약화된다.

## 10) P1-2 Best Practice 해결안

핵심 원칙: 보안 민감 작업(TOTP 해제)은 반드시 step-up 완료 세션(`tv=true`) 또는 동등한 재검증 절차를 요구한다.

### 10.1 disable 진입 조건을 "완전 인증 세션"으로 고정

- `_require_totp_verified_session(session_id)` 헬퍼를 도입해 `password_verified && totp_verified`를 강제한다.
- `disable_totp()`는 해당 헬퍼를 통과하지 못하면 즉시 거절한다.

### 10.2 코드 재검증이 필요하면 replay-safe 규칙 유지

- `code`를 계속 받을 경우 `verify_totp_code(..., last_verified_step=settings.totp_last_verified_step)`를 사용한다.
- `matched_step`를 `try_advance_totp_last_verified_step()`로 다시 전진시키지 못하면 실패 처리한다.
- 즉, `verify_totp()`와 동일한 anti-replay contract를 disable 경로에도 적용한다.

### 10.3 API/세션 계약 정리

- 가장 단순한 운영 모델: `disable_totp()`는 `tv=true` 세션만 요구하고 별도 `code` 입력은 제거.
- 또는 보수적 모델: `tv=true` + `code` 둘 다 요구(고위험 환경 권장).
- 어떤 모델을 택하든 계약을 단일화하고 테스트로 고정한다.

### 10.4 테스트 보강 (인증 단계 회귀 방지)

1. `password_verified=True`, `totp_verified=False` 세션으로 disable 요청 시 거절
2. `totp_verified=True` 세션으로 disable 성공
3. 동일 TOTP step 재사용 시 disable 실패(replay 차단)
4. TOTP 미설정 상태에서 disable 요청 시 기존 예외(`TotpNotConfiguredError`) 유지

## 11) 추가 Review 요약 (Frontend Filters P2)

- 날짜: 2026-02-18
- 코멘트 1: `[P2] Preserve selected filters when options list changes`
- 위치: `frontend/src/features/dashboard/components/filters/multi-select-filter.tsx:55-63`
- 현상: `MultiSelectFilter`가 `options` 기준으로만 체크박스 항목을 렌더링해, 서버 옵션에서 사라진 기존 선택값(stale selected value)을 개별 해제할 수 없다. 그 결과 필터가 보이지 않게 남아 결과를 계속 제한한다.
- 코멘트 2: `[P2] Do not scope options query by selected statuses`
- 위치: `frontend/src/features/dashboard/hooks/use-request-logs.ts:100-104`
- 현상: `/api/request-logs/options` 호출에 현재 `filters.statuses`를 함께 전송해, 백엔드가 상태 필터를 적용한 뒤 옵션을 계산한다. 상태 1개 선택 후 옵션이 해당 subset으로 수축되어 다중 선택 확장이 불가능해진다.

## 12) P2-1 문제 원인 (Stale 선택값 비노출)

- 필터 상태(source of truth)는 `selectedValues`인데, UI 렌더링 집합은 `options`로만 제한되어 있다.
- 서버 옵션은 현재 조건의 "가용 후보"일 뿐인데, 컴포넌트가 이를 "현재 활성 선택의 전체 집합"처럼 취급한다.
- 따라서 `selectedValues - options`에 해당하는 값이 존재하면, 상태에는 남아 있지만 UI에서는 제거 경로가 사라진다.

## 13) P2-1 Best Practice 해결안

핵심 원칙: Facet UI는 "현재 선택값의 가시성/해제 가능성"을 항상 보장해야 하며, 옵션 API 응답은 선택 상태를 덮어쓰지 않아야 한다.

### 13.1 렌더링 집합을 `options ∪ selectedValues`로 고정

- 표시 항목은 `options`와 `selectedValues`의 합집합으로 구성한다.
- `options`에 없는 선택값은 `isStale=true` 메타를 붙여 "현재 조건에서 미노출" 배지로 구분한다.
- stale 항목도 기존 선택과 동일하게 체크 해제 가능해야 한다.

예시 패턴:

```ts
const mergedOptions = mergeFacetOptions(options, selectedValues)
// stale option도 리스트에 남겨 개별 uncheck를 허용
```

### 13.2 개별 정리 액션을 명시적으로 제공

- stale 항목에 체크 해제 외에도 `x` 버튼(칩 제거) 또는 `stale 값만 정리` 액션을 제공한다.
- 사용자는 global reset 없이 문제 값을 즉시 제거할 수 있어야 한다.

### 13.3 상태 계약을 단일화

- 필터 활성 상태의 단일 SSOT는 `selectedValues`(또는 URL query state)로 유지한다.
- `options`는 추천/탐색용 데이터로만 사용하고, 선택 상태 동기화 기준으로 사용하지 않는다.

### 13.4 테스트 보강 (회귀 방지)

1. 선택된 값이 최신 `options`에서 사라져도 UI에 표시되고 개별 해제 가능
2. stale 값 해제 시 요청 파라미터에서 해당 값이 제거되고 결과가 즉시 반영
3. stale + normal 값 혼재 시 개별 토글 동작이 서로 간섭하지 않음
4. global reset 없이 stale 값만 제거 가능한 UX 경로 존재

## 14) P2-2 문제 원인 (Status Facet Self-Filtering)

- 옵션 조회 쿼리가 목록 조회와 동일 필터(`filters.statuses` 포함)를 재사용한다.
- 백엔드가 동일 status 조건을 적용해 status 옵션을 계산하면서, facet이 자기 자신으로 축소된다.
- 결과적으로 상태 필터는 "확장 가능한 다중 선택"이 아니라 "한 번 좁히면 복구 어려운 단일 선택"처럼 동작한다.

## 15) P2-2 Best Practice 해결안

핵심 원칙: Faceted search에서 각 facet 옵션은 "자기 facet 필터를 제외한 조건"으로 계산해야 한다.

### 15.1 옵션 조회 시 status self-filter를 제거

- `/api/request-logs/options` 호출 payload는 `listFilters`와 분리해 구성한다.
- status 옵션 계산 요청에는 `statuses`를 제외한 필터만 전달한다.

예시 패턴:

```ts
const optionsFilters = {
  ...filters,
  statuses: undefined, // 또는 필드 자체 omit
}
```

### 15.2 백엔드에 방어 로직 추가

- 프론트 회귀에 대비해, status 옵션 계산 시 서버에서도 `statuses` 조건을 무시한다.
- 즉, 계약을 "status facet은 self-filter 비적용"으로 API 레벨에서 고정한다.

### 15.3 필터 모델 분리로 계약 명확화

- 훅/서비스에서 `listFilters`(실제 결과 조회용)와 `facetFilters`(옵션 계산용)를 타입으로 분리한다.
- 동일 객체 재사용으로 인한 누락/혼입 회귀를 줄인다.

### 15.4 테스트 보강 (다중 선택 UX 보장)

1. 상태 1개 선택 후 options 재조회 시 다른 상태 항목이 계속 노출됨
2. 상태 외 필터(기간/모델/계정)는 options 축소에 정상 반영됨
3. status self-filter를 실수로 전송해도 서버 응답은 동일(무시됨)
4. 상태 다중 선택(추가/해제) 흐름이 끊기지 않고 유지됨

## 16) 기대 효과

- 옵션 변화에도 활성 필터의 가시성과 해제 가능성 보장
- status facet의 자연스러운 다중 선택 UX 복원
- 클라이언트/서버 양쪽에서 self-filter 회귀를 방지하는 이중 안전장치 확보
