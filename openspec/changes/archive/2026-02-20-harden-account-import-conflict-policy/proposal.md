## Why

`import_without_overwrite=true` 상태에서 동일 이메일 중복 계정이 생성된 뒤, 설정을 다시 `false`로 전환하면 이메일 기반 병합 대상이 2건 이상이 될 수 있다. 기존 구현은 이 상황에서 단일 row를 가정하여 런타임 예외를 유발하거나, import 실패 원인을 `invalid_auth_json`으로 오분류할 수 있다.

운영 관점에서 이 상태는 "어느 계정을 덮어쓸지 결정 불가"이므로 자동 병합보다 fail-fast가 안전하다.

## What Changes

- 계정 import 병합 정책을 명시한다.
  - ID가 정확히 일치하면 해당 계정을 업데이트한다.
  - ID 미일치 시 이메일 매치가 1건일 때만 병합한다.
  - 이메일 매치가 2건 이상이면 `409 duplicate_identity_conflict`로 실패한다.
- 계정 import API는 `invalid_auth_json`(400)과 identity conflict(409)를 구분한다.
- OAuth token persist 경로도 동일 conflict를 명시적 에러 상태로 처리한다.

## Impact

- **API**: `POST /api/accounts/import`에서 중복 identity 충돌 시 409 응답이 추가된다.
- **동작**: overwrite 재활성화 이후의 모호한 병합이 더 이상 암묵적으로 수행되지 않는다.
- **테스트**: repository/accounts API/oauth flow 회귀 테스트가 추가된다.
