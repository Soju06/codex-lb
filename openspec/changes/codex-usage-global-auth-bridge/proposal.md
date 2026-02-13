## Why

`/api/codex/usage`는 현재 로컬 계정 전체 usage를 집계해 반환하는 엔드포인트다. 이 경로를 대시보드 세션 인증에 묶으면 Codex 클라이언트(ChatGPT Bearer 기반) 사용 흐름이 깨진다.

## What Changes

- `/api/codex/usage`의 응답 의미(로컬 전체 집계)는 유지
- `/api/codex/usage` 접근은 대시보드 세션과 무관하게 Codex 호출자 인증(`Authorization: Bearer <chatgpt token>` + `chatgpt-account-id`)을 필수로 요구
- Codex 호출자 인증은 `chatgpt-account-id`가 LB 계정(`accounts.chatgpt_account_id`)에 존재하는지 확인하고, 업스트림 usage 호출로 토큰/계정 유효성을 검증
- `/api/codex/usage`는 API key 미들웨어 대상에서 제외하고, `/v1/*`와 `/backend-api/codex/*`만 API key 범위로 유지

## Impact

- 모든 모드에서 Codex rate limits 조회 인증 경로를 ChatGPT Bearer 기준으로 고정
- 기존 대시보드 집계 payload 계약 유지
- 인증 경계가 세션/API-key/ChatGPT Bearer로 명시적으로 분리
