## Why

`/api/codex/usage`는 현재 로컬 계정 전체 usage를 집계해 반환하는 엔드포인트다. 이 경로를 대시보드 세션 인증에 묶으면 Codex 클라이언트(ChatGPT Bearer 기반) 사용 흐름이 깨진다.

코드 리뷰에서 추가로 발견된 인증·인가 경계 문제:

1. **Legacy TOTP 보호 유실 (P1)**: `password_hash=NULL`이면 `/api/*` 인증을 즉시 우회하여, `totp_required_on_login=true`로 운영되던 인스턴스가 업그레이드 직후 공개 상태가 됨
2. **모델별 limit의 전역 lockout (P1)**: `validate_key()`가 `model_filter`가 설정된 limit도 무조건 검사하여, 특정 모델 quota 초과가 모든 요청을 429로 차단
3. **API key 편집 시 usage 초기화 (P2)**: edit dialog가 메타데이터만 수정해도 `limits`를 항상 PATCH payload에 포함하여, 백엔드에서 limit row가 재생성되고 usage counter/reset window가 초기화됨
4. **Limit 재생성으로 usage 상태 유실 (P1)**: `update_key()`에서 `limits_set=True`이면 기존 row 상태(`current_value`, `reset_at`)를 이어받지 않고 신규 row를 생성
5. **비지원 모델 목록 노출 (P2)**: `/v1/models`, `/backend-api/codex/models`가 `supported_in_api=false` 모델까지 노출

## What Changes

### 기존 (Auth Boundary)

- `/api/codex/usage`의 응답 의미(로컬 전체 집계)는 유지
- `/api/codex/usage` 접근은 대시보드 세션과 무관하게 Codex 호출자 인증(`Authorization: Bearer <chatgpt token>` + `chatgpt-account-id`)을 필수로 요구
- Codex 호출자 인증은 `chatgpt-account-id`가 LB 계정(`accounts.chatgpt_account_id`)에 존재하는지 확인하고, 업스트림 usage 호출로 토큰/계정 유효성을 검증
- `/api/codex/usage`는 API key 미들웨어 대상에서 제외하고, `/v1/*`와 `/backend-api/codex/*`만 API key 범위로 유지

### 추가 (Review Findings)

- 대시보드 세션 인증에서 `password_hash`와 `totp_required_on_login`을 함께 평가하여 인증 필요 여부를 결정하고, 불일치 상태(`password_hash=NULL`, `totp_required_on_login=true`)는 fail-closed로 처리
- API key `validate_key()` 시 `model_filter`가 설정된 limit는 요청 모델 컨텍스트가 일치할 때만 적용하고, 인증 검증과 limit 판정을 분리
- 프론트엔드 API key edit dialog에서 limits 변경 여부를 dirty-check 후 조건부 전송하여, 메타데이터만 변경 시 `limits` payload 제외
- 백엔드 limit 교체를 상태 보존형 upsert로 전환하여, 동일 키(`limit_type`, `limit_window`, `model_filter`)의 기존 rule은 `current_value`/`reset_at` 유지
- `/v1/models`, `/backend-api/codex/models` 응답에서 `supported_in_api=false` 모델을 필터링하는 단일 predicate를 모든 모델 목록 엔드포인트에 적용

## Impact

- 모든 모드에서 Codex rate limits 조회 인증 경로를 ChatGPT Bearer 기준으로 고정
- 기존 대시보드 집계 payload 계약 유지
- 인증 경계가 세션/API-key/ChatGPT Bearer로 명시적으로 분리
- legacy 업그레이드 구간에서 인증 우회(fail-open) 제거
- 모델별 quota 정책의 의미를 정확히 보장
- API key 메타데이터 편집 시 quota usage 의도치 않은 초기화 제거
- 인증/인가/limit 계약 분리로 유지보수성 향상
