## Context

기존 import 경로는 `auth.json` 업로드 전제이며, credential 목록에서 자동으로 OAuth/device 로그인과 2FA를 완료하는 경로가 없다. 따라서 운영자가 계정 수만큼 수동 브라우저 작업을 반복해야 한다.

## Goals / Non-Goals

### Goals

- credentials 텍스트 한 번으로 여러 계정을 순차 인증/저장한다.
- 입력 형식 오류는 즉시 차단하고, 실행 오류는 계정 단위로 보고한다.
- 기존 account upsert/overwrite 정책을 그대로 재사용한다.
- 기존 `auth.json` import UX/API는 유지한다.

### Non-Goals

- DB에 password/2fa secret 저장
- 기존 OAuth browser/device 수동 플로우 제거
- 외부 anti-bot/Cloudflare challenge 우회 로직 보장

## API Design

### Request

`POST /api/accounts/import-credentials`

```json
{
  "credentialsText": "email1:password1:totpsecret1\nemail2:password2:totpsecret2"
}
```

### Response

```json
{
  "total": 2,
  "imported": 1,
  "failed": 1,
  "results": [
    {
      "line": 1,
      "email": "email1@example.com",
      "status": "imported",
      "accountId": "acc_..."
    },
    {
      "line": 2,
      "email": "email2@example.com",
      "status": "failed",
      "error": "authorization timed out"
    }
  ]
}
```

## Backend Flow

1. 입력 텍스트를 라인별 파싱 (`email:password:2fa_secret`).
2. line format 에러 발생 시 전체 요청 `400 invalid_credentials_format`.
3. 각 계정에 대해 자동 인증 수행:
   - OAuth authorize + PKCE HTTP flow 시작
   - `/api/accounts/authorize/continue`, `/password/verify`, `/mfa/*` 순서로 credential + OTP 제출
   - anti-bot 회피를 위해 브라우저 지문 impersonation HTTP client를 사용
   - consent/workspace redirect를 거쳐 authorization code 획득 후 token exchange
4. 토큰을 기존 upsert 정책으로 저장.
5. 계정별 결과를 집계해 응답.

## Frontend Flow

1. Import dialog에서 모드 선택 (`auth.json` / credentials text).
2. credentials 모드 submit 시 `POST /api/accounts/import-credentials` 호출.
3. 성공/실패 건수와 상세 결과를 노출.
4. 완료 후 accounts list query invalidate.

## Error Semantics

- `invalid_credentials_format` (400): 파싱 불가/빈 입력/필수 필드 누락
- 200 + per-item `status=failed`: 개별 인증 실패(자격증명 오류, OTP 오류, timeout 등)

## Risk

- 외부 auth API 시그널/anti-bot 정책 변경 시 자동화가 실패할 수 있다.
- mitigations:
  - 계정 단위 실패 분리
  - 명확한 오류 메시지 반환
  - 수동 OAuth 플로우를 fallback 경로로 유지
