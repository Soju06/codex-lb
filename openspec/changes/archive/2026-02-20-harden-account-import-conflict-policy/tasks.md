## 1. 정책 반영

- [x] 1.1 `AccountsRepository.upsert()`에서 이메일 병합 대상을 "단일 매치만 허용"으로 변경
- [x] 1.2 이메일 다중 매치 시 도메인 예외를 발생시키고 모호한 자동 병합 금지

## 2. API 에러 계약 정리

- [x] 2.1 accounts import 라우트에서 `invalid_auth_json`(400)과 `duplicate_identity_conflict`(409)를 분리
- [x] 2.2 광범위 예외 캐치(`except Exception`) 제거

## 3. OAuth 경로 안정화

- [x] 3.1 OAuth device/browser persist 경로에서 identity conflict를 명시적 error 상태로 처리

## 4. 테스트

- [x] 4.1 repository 회귀 테스트: merge enabled + email 다중 매치 시 conflict
- [x] 4.2 accounts API 회귀 테스트: no-overwrite로 중복 생성 후 overwrite 복귀 시 409
- [x] 4.3 oauth flow 회귀 테스트: 동일 conflict 조건에서 status=error
