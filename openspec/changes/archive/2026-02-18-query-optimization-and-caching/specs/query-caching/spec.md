## ADDED Requirements

### Requirement: Rate limit headers cache
프록시 요청 경로에서 rate limit 헤더 계산 결과를 TTL 기반으로 캐시한다. 캐시 TTL은 usage refresh interval과 동기화되며, usage refresh 완료 시 캐시가 즉시 invalidate된다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: 캐시 TTL 내 재요청 시 DB 쿼리 없이 반환
- **WHEN** rate limit 헤더가 TTL 내에 이미 캐시되어 있을 때 프록시 요청이 들어오면
- **THEN** DB 쿼리 없이 캐시된 헤더를 반환해야 한다 (SHALL)

#### Scenario: usage refresh 완료 시 캐시 invalidate
- **WHEN** 백그라운드 usage refresh 스케줄러가 refresh 사이클을 완료하면
- **THEN** rate limit 헤더 캐시가 invalidate되어 다음 요청에서 최신 데이터로 재계산된다 (SHALL)

#### Scenario: 캐시 미스 시 DB에서 계산
- **WHEN** 캐시가 비어있거나 TTL이 만료되었을 때 프록시 요청이 들어오면
- **THEN** DB에서 rate limit 데이터를 조회하여 헤더를 계산하고 캐시에 저장한다 (SHALL)

### Requirement: Settings 캐시 활용
프록시 요청 경로에서 dashboard settings 조회 시 별도 DB 세션을 열지 않고 기존 `SettingsCache`를 활용한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: 프록시 요청 시 settings 캐시 사용
- **WHEN** stream 또는 compact 프록시 요청이 settings 값(sticky_threads_enabled, prefer_earlier_reset_accounts)을 필요로 할 때
- **THEN** `SettingsCache`에서 읽어야 하며, 별도 DB 세션을 생성하지 않아야 한다 (SHALL)

### Requirement: 계정 선택 시 중복 쿼리 제거
`LoadBalancer.select_account()`에서 usage refresh가 실행되지 않은 경우 `latest_by_account()` 재호출을 생략한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: refresh 미발생 시 기존 usage 데이터 재사용
- **WHEN** `refresh_accounts()`가 모든 계정을 스킵하여 실제 갱신이 발생하지 않았을 때
- **THEN** 이전에 조회한 `latest_by_account()` 결과를 재사용하고 추가 쿼리를 실행하지 않아야 한다 (SHALL)

#### Scenario: refresh 발생 시 최신 데이터 재조회
- **WHEN** `refresh_accounts()`가 하나 이상의 계정 usage를 갱신했을 때
- **THEN** `latest_by_account()`를 다시 호출하여 갱신된 데이터를 반영해야 한다 (SHALL)

### Requirement: latest_by_account 쿼리 효율화
usage_history에서 계정당 최신 레코드 조회 시 전체 테이블 로드 대신 DB 레벨에서 필터링한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: 계정당 최신 1건만 반환
- **WHEN** `latest_by_account(window)`가 호출되면
- **THEN** SQL 서브쿼리로 계정당 최신 1건만 조회하며, 전체 row를 Python으로 로드하지 않아야 한다 (SHALL)
- **AND** 결과 형태(dict[str, UsageHistory])는 기존과 동일해야 한다 (SHALL)

### Requirement: Request logs 단일 쿼리 조회
request_logs 목록 조회 시 list와 count를 단일 쿼리로 통합한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: 페이지네이션 시 list + total을 한 번에 조회
- **WHEN** request logs 목록 API가 호출되면
- **THEN** window function으로 rows와 total count를 단일 쿼리에서 반환해야 한다 (SHALL)
- **AND** API 응답 형태(requests, total, has_more)는 기존과 동일해야 한다 (SHALL)

### Requirement: Refreshed 플래그 정확성
`refresh_accounts()`의 `refreshed` 값은 "usage_history가 실제로 변경되었는지"만 표현해야 한다. `_refresh_account()`는 `AccountRefreshResult(usage_written: bool)` 타입을 반환한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: DB 쓰기 없이 종료 시 refreshed=False
- **WHEN** `_refresh_account()`가 payload 없음, rate limit 정보 없음, 또는 API 오류로 DB 쓰기 없이 종료되면
- **THEN** `refresh_accounts()`는 해당 계정에 대해 `refreshed`를 `True`로 설정하지 않아야 한다 (SHALL)

#### Scenario: Row 생성 시 refreshed=True
- **WHEN** `_refresh_account()`가 primary 또는 secondary usage row를 1건 이상 생성하면
- **THEN** `refresh_accounts()`는 `refreshed=True`를 반환해야 한다 (SHALL)

#### Scenario: Refreshed=False 시 재조회 억제
- **WHEN** `LoadBalancer.select_account()`에서 `refreshed=False`일 때
- **THEN** `latest_by_account()`를 재호출하지 않고 기존 결과를 재사용해야 한다 (SHALL)

### Requirement: API Key Quota 원자적 검증
API key quota 검증과 차감(또는 예약)은 단일 원자 연산으로 처리되어야 한다. 병렬 요청이 동시에 stale counter를 통과하여 quota를 초과하는 것을 방지한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: 원자적 예약 성공
- **WHEN** quota에 여유가 있고 `try_reserve_usage()`가 호출되면
- **THEN** `current_value`를 원자적으로 증가시키고 성공을 반환해야 한다 (SHALL)
- **AND** 조건부 UPDATE(CAS)로 동시성을 보장해야 한다 (SHALL)

#### Scenario: 원자적 예약 실패 시 429
- **WHEN** `current_value + delta > max_value`이면
- **THEN** UPDATE가 0 rows affected를 반환하고 서비스는 429를 반환해야 한다 (SHALL)

#### Scenario: 병렬 요청 시 quota 초과 방지
- **WHEN** 한계치 직전 key에 병렬 요청 N개가 동시에 전송되면
- **THEN** 허용된 요청 건수의 합이 quota를 초과하지 않아야 한다 (SHALL)

#### Scenario: Upstream 실패 시 예약 해제
- **WHEN** 예약 성공 후 upstream 요청이 실패하면
- **THEN** 예약된 사용량이 원복되어야 한다 (SHALL)

#### Scenario: Finalize 멱등성
- **WHEN** 동일 `usage_reservation_id`로 finalize가 중복 호출되면
- **THEN** 사용량은 한 번만 반영되어야 한다 (SHALL)

### Requirement: TOTP Disable Step-Up 세션 요구
`disable_totp()`는 완전 인증 세션(`password_verified=true` AND `totp_verified=true`)을 요구해야 한다. 비밀번호만 검증된 세션으로는 TOTP 해제가 불가능해야 한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: 비완전 인증 세션으로 disable 거절
- **WHEN** `password_verified=True`이고 `totp_verified=False`인 세션으로 `disable_totp()`가 호출되면
- **THEN** 요청이 거절되어야 한다 (SHALL)

#### Scenario: 완전 인증 세션으로 disable 허용
- **WHEN** `password_verified=True`이고 `totp_verified=True`인 세션으로 `disable_totp()`가 호출되면
- **THEN** TOTP 해제가 처리되어야 한다 (SHALL)

#### Scenario: TOTP 코드 replay 차단
- **WHEN** `disable_totp()` 경로에서 코드 재검증이 필요할 때
- **AND** 이미 사용된 TOTP step의 코드가 제출되면
- **THEN** disable이 실패해야 한다 (SHALL)

### Requirement: Stale 필터 선택값 가시성
`MultiSelectFilter`는 서버 옵션에서 사라진 기존 선택값(stale selected value)도 UI에 표시하고 개별 해제를 허용해야 한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: Stale 선택값 표시 및 해제
- **WHEN** 선택된 값이 최신 `options`에서 사라졌을 때
- **THEN** 해당 값이 stale 표시와 함께 UI에 계속 렌더링되어야 한다 (SHALL)
- **AND** 사용자가 개별적으로 해제할 수 있어야 한다 (SHALL)

#### Scenario: Stale 값 해제 시 결과 반영
- **WHEN** stale 선택값이 해제되면
- **THEN** 요청 파라미터에서 해당 값이 즉시 제거되고 결과에 반영되어야 한다 (SHALL)

### Requirement: Status Facet Self-Filtering 방지
Status facet 옵션 조회 시 자기 자신의 필터(statuses)를 적용하지 않아야 한다. 이를 통해 다중 선택 UX를 보장한다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: Status 선택 후 다른 상태 항목 유지
- **WHEN** 상태 1개가 선택된 후 options가 재조회되면
- **THEN** 선택되지 않은 다른 상태 항목도 계속 노출되어야 한다 (SHALL)

#### Scenario: 백엔드 status self-filter 무시
- **WHEN** 클라이언트가 실수로 `statuses`를 옵션 조회에 포함해도
- **THEN** 서버는 status 옵션 계산 시 해당 조건을 무시해야 한다 (SHALL)

#### Scenario: 상태 외 필터의 정상 축소
- **WHEN** 기간/모델/계정 등 status 외 필터가 적용되면
- **THEN** 해당 필터는 옵션 축소에 정상적으로 반영되어야 한다 (SHALL)
