## Context

`import_without_overwrite=true`에서 동일 이메일 중복 계정이 생성된 뒤 overwrite 모드로 복귀하면, 이메일 기반 병합 대상이 다건이 될 수 있다. 이 상태에서 임의 선택 병합은 데이터 손실/예상치 못한 덮어쓰기를 유발하므로 금지해야 한다.

## Goals

- overwrite 모드에서 병합 대상을 단일 row로만 허용한다.
- 모호한 상태는 fail-fast로 차단하고 원인 코드(`duplicate_identity_conflict`)를 명확히 노출한다.
- accounts import와 oauth token persist 경로의 정책을 일관화한다.

## Decision

### 1) Canonical selection rules

`AccountsRepository.upsert()`에서 병합 판단 순서를 아래와 같이 고정한다.

1. 입력 `account.id`가 존재하면 해당 row를 업데이트한다.
2. `account.id`가 없고 overwrite 모드인 경우, 이메일 매치 row를 조회한다.
3. 이메일 매치가 0건이면 신규 생성한다.
4. 이메일 매치가 1건이면 해당 row를 업데이트한다.
5. 이메일 매치가 2건 이상이면 `AccountIdentityConflictError`를 발생시킨다.

이로써 overwrite 모드에서 "임의 row 선택"이 발생하지 않는다.

### 2) Error mapping

- auth.json 파싱/스키마 오류는 `InvalidAuthJsonError`로 통일한다.
- identity 모호성은 `AccountIdentityConflictError`로 구분한다.
- API 매핑:
  - `InvalidAuthJsonError` -> `400 invalid_auth_json`
  - `AccountIdentityConflictError` -> `409 duplicate_identity_conflict`

### 3) OAuth behavior

OAuth device/browser flow의 token persist 경로에서도 동일 예외를 처리한다. conflict 발생 시 flow는 `status=error`로 종료되며, background task 예외 누수를 방지한다.

## Trade-offs

- overwrite 모드에서 기존 다중 중복을 자동 정리하지 않는다. 대신 충돌을 드러내고 명시적 운영 액션(중복 정리 또는 no-overwrite 재활성화)을 요구한다.
- 단기적으로 import가 실패할 수 있지만, 잘못된 계정 덮어쓰기 위험을 제거한다.

## Validation

- repository 통합 테스트: ambiguous email + merge enabled 시 conflict 예외
- accounts API 통합 테스트: overwrite 복귀 후 ambiguous import 시 409
- oauth flow 통합 테스트: 동일 조건에서 status=error
