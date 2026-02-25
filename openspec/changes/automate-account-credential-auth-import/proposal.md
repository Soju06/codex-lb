## Why

현재 계정 import는 `auth.json` 업로드 또는 수동 OAuth만 지원한다. 운영에서는 `email:password:2fa_secret` 형식의 계정 목록을 한 번에 넣고 로그인/2FA/토큰 저장까지 자동화해야 한다.

현재 구조에서는 다음 문제가 있다.

- 대량 계정 onboarding 시 수동 단계가 많아 시간이 오래 걸린다.
- 계정별 실패 원인을 UI/API에서 구조적으로 확인하기 어렵다.
- credential 기반 import 계약이 없어 프론트/백엔드 동작이 고정되지 않는다.

## What Changes

- `POST /api/accounts/import-credentials` 엔드포인트 추가
  - 입력: multi-line credentials 텍스트 (`email:password:2fa_secret`)
  - 출력: 계정별 성공/실패 결과 요약
- 계정 서비스에 credential 파서와 자동 인증 오케스트레이션 추가
  - 형식 검증 실패 시 fail-fast (`400 invalid_credentials_format`)
  - 인증 성공 시 기존 account upsert 정책을 재사용
- OAuth HTTP 인증 자동화 클라이언트 추가
  - credential + 2FA secret으로 authorize/MFA/consent를 처리하고 토큰 획득
  - Cloudflare 403 완화를 위해 browser impersonation HTTP transport 사용
  - 계정별 실패를 결과 목록에 반영
- Accounts import UI 확장
  - 기존 `auth.json` 업로드 유지
  - credentials 텍스트 입력 모드 추가
  - 결과 요약 표시 및 계정 목록 refresh

## Capabilities

### Added Capabilities

- `accounts-auth-automation`: credential 목록 import + end-to-end 자동 인증

### Modified Capabilities

- `frontend-architecture`: Accounts page import UX에 credentials import 시나리오 추가

## Impact

- **Backend**
  - `app/modules/accounts/{api.py,schemas.py,service.py}`
  - 신규 자동 인증 클라이언트/타입 모듈 추가
- **Frontend**
  - `frontend/src/features/accounts/{schemas.ts,api.ts,hooks,use-accounts.ts,components/import-dialog.tsx}`
- **Tests**
  - backend unit/integration 회귀 테스트 추가
  - frontend import dialog/api 계약 테스트 추가
