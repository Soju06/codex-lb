## 1. Spec + Contracts

- [x] 1.1 `accounts-auth-automation` capability delta spec 추가
- [x] 1.2 `frontend-architecture` Accounts import requirement delta 반영

## 2. Backend

- [x] 2.1 credentials batch request/response 스키마 추가
- [x] 2.2 `email:password:2fa_secret` 파서 구현 (line 번호 포함 에러)
- [x] 2.3 자동 인증 오케스트레이션 구현 및 계정별 결과 집계
- [x] 2.4 `POST /api/accounts/import-credentials` 라우트 추가 및 에러 매핑

## 3. Frontend

- [x] 3.1 Accounts import dialog에 credentials 입력 모드 추가
- [x] 3.2 credentials import API + Zod 스키마 추가
- [x] 3.3 mutation/토스트/리프레시 연결 및 결과 표시

## 4. Tests

- [x] 4.1 backend unit: parser + 서비스 결과 집계
- [x] 4.2 backend integration: `/api/accounts/import-credentials` 성공/형식오류 케이스
- [x] 4.3 frontend tests: import dialog 모드 전환 + credentials submit 계약

## 5. Verification

- [x] 5.1 `uv run ruff check .`
- [x] 5.2 `uv run ty check`
- [x] 5.3 `uv run pytest tests/unit tests/integration`
- [x] 5.4 `cd frontend && npm run lint && npm run test`
- [ ] 5.5 `openspec validate --specs`
