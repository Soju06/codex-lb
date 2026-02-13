## ADDED Requirements

### Requirement: Rate limit headers cache
프록시 요청 경로에서 rate limit 헤더 계산 결과를 TTL 기반으로 캐시한다. 캐시 TTL은 usage refresh interval과 동기화되며, usage refresh 완료 시 캐시가 즉시 invalidate된다.

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
프록시 요청 경로에서 dashboard settings 조회 시 별도 DB 세션을 열지 않고 기존 `SettingsCache`를 활용한다.

#### Scenario: 프록시 요청 시 settings 캐시 사용
- **WHEN** stream 또는 compact 프록시 요청이 settings 값(sticky_threads_enabled, prefer_earlier_reset_accounts)을 필요로 할 때
- **THEN** `SettingsCache`에서 읽어야 하며, 별도 DB 세션을 생성하지 않아야 한다 (SHALL)

### Requirement: 계정 선택 시 중복 쿼리 제거
`LoadBalancer.select_account()`에서 usage refresh가 실행되지 않은 경우 `latest_by_account()` 재호출을 생략한다.

#### Scenario: refresh 미발생 시 기존 usage 데이터 재사용
- **WHEN** `refresh_accounts()`가 모든 계정을 스킵하여 실제 갱신이 발생하지 않았을 때
- **THEN** 이전에 조회한 `latest_by_account()` 결과를 재사용하고 추가 쿼리를 실행하지 않아야 한다 (SHALL)

#### Scenario: refresh 발생 시 최신 데이터 재조회
- **WHEN** `refresh_accounts()`가 하나 이상의 계정 usage를 갱신했을 때
- **THEN** `latest_by_account()`를 다시 호출하여 갱신된 데이터를 반영해야 한다 (SHALL)

### Requirement: latest_by_account 쿼리 효율화
usage_history에서 계정당 최신 레코드 조회 시 전체 테이블 로드 대신 DB 레벨에서 필터링한다.

#### Scenario: 계정당 최신 1건만 반환
- **WHEN** `latest_by_account(window)`가 호출되면
- **THEN** SQL 서브쿼리로 계정당 최신 1건만 조회하며, 전체 row를 Python으로 로드하지 않아야 한다 (SHALL)
- **AND** 결과 형태(dict[str, UsageHistory])는 기존과 동일해야 한다 (SHALL)

### Requirement: Request logs 단일 쿼리 조회
request_logs 목록 조회 시 list와 count를 단일 쿼리로 통합한다.

#### Scenario: 페이지네이션 시 list + total을 한 번에 조회
- **WHEN** request logs 목록 API가 호출되면
- **THEN** window function으로 rows와 total count를 단일 쿼리에서 반환해야 한다 (SHALL)
- **AND** API 응답 형태(requests, total, has_more)는 기존과 동일해야 한다 (SHALL)
