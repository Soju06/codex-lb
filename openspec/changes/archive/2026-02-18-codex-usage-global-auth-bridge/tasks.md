## 1. Auth Boundary

- [x] 1.1 Update middleware so `/api/codex/usage` always requires codex bearer caller validation and is not unlocked by dashboard session auth
- [x] 1.2 Keep `/v1/*` and `/backend-api/codex/*` under API key middleware scope

## 2. Codex Bearer Validation

- [x] 2.1 Add repository lookup for active LB membership by `chatgpt_account_id`
- [x] 2.2 Validate bearer token/account pair via upstream usage call before allowing `/api/codex/usage`

## 3. Tests (Auth Boundary)

- [x] 3.1 Add integration test: password mode + dashboard session only (no codex caller identity) => 401
- [x] 3.2 Add integration test: password mode + logged out + valid bearer/account-id + registered `chatgpt_account_id` => 200
- [x] 3.3 Add integration test: password mode + logged out + unknown `chatgpt_account_id` => 401
- [x] 3.4 Keep existing `/api/codex/usage` aggregate behavior assertions intact

## 4. Legacy TOTP Protection (P1)

- [x] 4.1 `_validate_dashboard_session()`에서 `requires_auth = (password_hash is not None) or totp_required_on_login`으로 인증 필요 조건 통합 — `password_hash=NULL`이어도 `totp_required_on_login=true`면 인증 필수
- [x] 4.2 세션 검증 단계 분리: `requires_auth=True`이면 유효 세션 필수(없으면 401), `password_hash is not None`이면 `password_verified` 필수, `totp_required_on_login=True`이면 `totp_verified` 필수
- [x] 4.3 마이그레이션 불일치 상태(`password_hash=NULL && totp_required_on_login=true`) 경고 로그/메트릭 추가
- [x] 4.4 회귀 테스트: `password_hash=NULL`, `totp_required_on_login=true`, 세션 없음 → 401
- [x] 4.5 회귀 테스트: `password_hash=NULL`, `totp_required_on_login=true`, `pw=false,tv=true` 세션 → 200
- [x] 4.6 회귀 테스트: `password_hash=NULL`, `totp_required_on_login=true`, `pw=true,tv=false` 세션 → 401 (`totp_required`)

## 5. Model-Filtered Limit 범위 수정 (P1)

- [x] 5.1 `validate_key()`에서 인증 검증과 limit 판정을 분리 — 미들웨어는 키 유효성(존재/활성/만료/기본 reset)만 수행
- [x] 5.2 요청 모델을 아는 지점(예: `proxy/api.py` payload 파싱 후)에서 quota 판정 수행: `model_filter is None`(global) 또는 `model_filter == request_model`(model-scoped)일 때만 적용
- [x] 5.3 서비스 계약을 `enforce_limits_for_request(key_id, *, request_model: str | None)` 타입으로 명확화
- [x] 5.4 회귀 테스트: `model_filter="gpt-5.1"` limit 소진 + 요청 모델 `gpt-4o-mini` → 허용
- [x] 5.5 회귀 테스트: `model_filter="gpt-5.1"` limit 소진 + 요청 모델 `gpt-5.1` → 429
- [x] 5.6 회귀 테스트: `model_filter="gpt-5.1"` limit 소진 + `/v1/models` → 허용
- [x] 5.7 회귀 테스트: global limit 소진 시 모든 프록시 요청 429 유지

## 6. API Key Edit Limits Dirty-Check (P2)

- [x] 6.1 프론트엔드 `api-key-edit-dialog.tsx`에서 초기 limits와 현재 폼 값을 정규화(normalize) 비교하여, 실제 차이가 있을 때만 `limits`를 PATCH payload에 포함
- [x] 6.2 메타데이터(name, is_active)만 변경 시 `limits` 필드를 payload에서 제외
- [x] 6.3 회귀 테스트: 이름만 수정한 PATCH → `limits` 미전송, 기존 `current_value`/`reset_at` 유지
- [x] 6.4 회귀 테스트: 활성 상태만 수정한 PATCH → `limits` 미전송, usage 상태 유지
- [x] 6.5 회귀 테스트: limit 값 실제 변경 PATCH → `limits` 전송, 의도한 정책 교체만 발생
- [x] 6.6 회귀 테스트: 순서만 다른 동일 rule set 제출 → 변경 없음 판단(`limits` 미전송)

## 7. Limit 상태 보존형 Upsert (P1)

- [x] 7.1 `update_key()` 내 limit 교체 경로를 상태 보존형 upsert로 전환 — 비교 키 `(limit_type, limit_window, model_filter)`로 기존 rule 매칭
- [x] 7.2 동일 키의 기존 rule이 있으면 `current_value`/`reset_at` 유지한 채 `max_value`만 갱신, 새로 추가된 rule만 `current_value=0`으로 시작, 삭제된 rule만 제거
- [x] 7.3 사용량 초기화를 명시적 액션(`reset_usage` 필드 또는 별도 엔드포인트)으로만 허용
- [x] 7.4 회귀 테스트: 이름/활성 상태만 변경한 PATCH → 기존 `current_value`/`reset_at` 유지
- [x] 7.5 회귀 테스트: 동일 limit 정책 재전송 PATCH → usage 상태 유지
- [x] 7.6 회귀 테스트: `max_value` 조정 PATCH → `current_value`/`reset_at` 유지, 임계치 판정만 변경
- [x] 7.7 회귀 테스트: 명시적 reset 액션에서만 usage 초기화 발생

## 8. 비지원 모델 목록 필터링 (P2)

- [x] 8.1 `is_public_model(model, allowed_models)` 헬퍼 생성 — `supported_in_api=True` && `allowed_models` 포함 조건을 단일 predicate로 통합
- [x] 8.2 `/v1/models`, `/backend-api/codex/models`, `/api/models` 모든 엔드포인트에서 동일 필터 적용
- [x] 8.3 회귀 테스트: `supported_in_api=false` 모델이 snapshot에 포함 시 `/v1/models` 응답에서 제외
- [x] 8.4 회귀 테스트: `supported_in_api=false` 모델이 `/backend-api/codex/models` 응답에서도 제외
- [x] 8.5 회귀 테스트: `allowed_models`에 포함돼도 `supported_in_api=false`면 노출되지 않음
- [x] 8.6 회귀 테스트: `/api/models`와 OpenAI 호환 모델 목록 간 노출 집합 일치
