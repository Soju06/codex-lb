# Review Context: codex-usage-global-auth-bridge

## 1) Review 요약

- 날짜: 2026-02-18
- 코멘트 1: `[P1] Preserve legacy TOTP protection during auth migration`
- 위치: `app/core/middleware/dashboard_auth.py:57-58`
- 현상: `password_hash`가 `NULL`이면 `/api/*` 인증을 즉시 우회하여, 구버전에서 `totp_required_on_login=true`로 운영되던 인스턴스가 업그레이드 직후 공개 상태가 될 수 있음.

- 코멘트 2: `[P1] Ignore model-filtered limits when validating generic API keys`
- 위치: `app/modules/api_keys/service.py:241-243`
- 현상: 모델 한정 limit(`model_filter`)이 소진되어도 모든 요청을 전역 429로 차단하여, 모델별 limit가 사실상 글로벌 lockout으로 동작함.

- 코멘트 3: `[P2] Avoid sending unchanged limits in API key edit payload`
- 위치: `frontend/src/features/api-keys/components/api-key-edit-dialog.tsx:69-76`
- 현상: edit dialog가 메타데이터만 수정해도 `limits`를 PATCH payload에 항상 포함하고, 백엔드에서 `limits_set=True`로 전체 교체가 발생해 usage counter와 reset window가 의도치 않게 초기화됨.

## 2) P1-1 문제 원인 (Legacy TOTP 보호 유실)

현재 `_validate_dashboard_session()`는 `settings.password_hash is None`이면 조기 `return None`한다.  
이 분기 때문에 `totp_required_on_login=true` 상태를 전혀 고려하지 못하고, 마이그레이션 중 불일치 상태(`password_hash=NULL`, `totp_required_on_login=true`)에서 fail-open이 발생한다.

## 3) P1-1 Best Practice 해결안

핵심 원칙: 인증 정책 불일치 상태는 항상 fail-closed로 처리하고, 마이그레이션 중에도 보안 강도를 낮추지 않는다.

### 3.1 인증 필요 조건을 단일 식으로 고정

`password_hash`와 `totp_required_on_login`을 함께 평가하여 인증 필요 여부를 결정한다.

```python
requires_auth = (settings.password_hash is not None) or settings.totp_required_on_login
if not requires_auth:
    return None
```

### 3.2 세션 검증을 단계별로 분리

- `requires_auth=True`이면 유효 세션이 없을 때 401
- `password_hash is not None`이면 `password_verified` 필수
- `totp_required_on_login=True`이면 `totp_verified` 필수

이렇게 하면 legacy 상태에서도 최소 `tv=true` 세션 없이는 `/api/*` 접근이 불가능하다.

### 3.3 마이그레이션 불일치 상태를 명시적으로 표면화

- 로그/메트릭에 `password_hash=NULL && totp_required_on_login=true`를 별도 경고로 남긴다.
- 운영자가 빠르게 정상 상태(비밀번호 설정 후 TOTP 유지 또는 정책 변경)로 수렴할 수 있도록 `/api/dashboard-auth/session` 응답에 migration 안내 신호를 추가하는 방식을 권장한다.

### 3.4 회귀 테스트 보강

1. `password_hash=NULL`, `totp_required_on_login=true`, 세션 없음 -> `/api/settings` 401
2. `password_hash=NULL`, `totp_required_on_login=true`, `pw=false,tv=true` 세션 -> `/api/settings` 200
3. `password_hash=NULL`, `totp_required_on_login=true`, `pw=true,tv=false` 세션 -> `/api/settings` 401 (`totp_required`)

## 4) P1-2 문제 원인 (model_filter limit 범위 오적용)

`validate_key()`가 요청 모델 컨텍스트 없이 모든 limit를 검사한다.  
이때 `model_filter`가 설정된 limit도 무조건 검사되어, 특정 모델 quota 초과가 다른 모델 요청과 `/v1/models`까지 차단하는 부작용이 생긴다.

## 5) P1-2 Best Practice 해결안

핵심 원칙: limit 검증은 "요청 컨텍스트(모델)"를 포함한 정책 평가여야 하며, 인증과 quota 판정을 분리한다.

### 5.1 인증 검증과 limit 판정을 분리

- 미들웨어의 `validate_key()`는 키 유효성(존재/활성/만료/기본 reset)만 수행
- quota 판정은 요청 모델을 아는 지점(예: `proxy/api.py`에서 payload 파싱 후)에서 수행

### 5.2 적용 대상 limit를 명시적으로 필터링

판정 규칙:

- `limit.model_filter is None` -> 항상 적용 (global limit)
- `limit.model_filter == request_model` -> 적용 (model-scoped limit)
- 그 외 -> 이번 요청에는 미적용

`/v1/models`처럼 모델이 없는 요청은 global limit만 평가한다.

### 5.3 서비스 계약을 타입으로 명확화

예시:

```python
async def enforce_limits_for_request(self, key_id: str, *, request_model: str | None) -> None: ...
```

`request_model`을 명시 인자로 받도록 계약을 고정하면, model-scoped 정책이 전역으로 새는 회귀를 줄일 수 있다.

### 5.4 회귀 테스트 보강

1. `model_filter="gpt-5.1"` limit 소진 + 요청 모델 `gpt-4o-mini` -> 허용
2. `model_filter="gpt-5.1"` limit 소진 + 요청 모델 `gpt-5.1` -> 429
3. `model_filter="gpt-5.1"` limit 소진 + `/v1/models` -> 허용
4. global limit 소진 시에는 모든 프록시 요청이 429 유지

## 6) P2 문제 원인 (Edit payload에서 limits 무조건 전송)

`api-key-edit-dialog.tsx`의 PATCH 요청 생성 로직이 `limits` 변경 여부와 무관하게 항상 `limits`를 포함한다.  
백엔드는 `limits` 필드 존재 자체를 "제한 정책 교체 의도"로 해석해 `limits_set=True` 경로로 진입하므로, 단순 이름/활성화 수정에서도 limit row 재생성이 일어나 사용량 상태가 초기화된다.

## 7) P2 Best Practice 해결안

핵심 원칙: PATCH는 의도한 변경만 담아야 하며, quota 상태를 바꾸는 필드는 명시적 사용자 액션일 때만 전송한다.

### 7.1 프론트엔드에서 limits dirty-check 후 조건부 전송

- 초기 값과 현재 폼 값을 동일 규칙으로 정규화(normalize)해서 비교한다.
- 실제 차이가 있을 때만 payload에 `limits`를 포함한다.
- 메타데이터(name, is_active)만 바뀐 경우 `limits`를 payload에서 제거한다.

예시:

```ts
const normalizedInitial = normalizeLimits(initialLimits)
const normalizedCurrent = normalizeLimits(formValues.limits)
const limitsChanged = JSON.stringify(normalizedInitial) !== JSON.stringify(normalizedCurrent)

const payload: UpdateApiKeyRequest = {
  name: formValues.name,
  is_active: formValues.isActive,
  ...(limitsChanged ? { limits: normalizedCurrent } : {}),
}
```

### 7.2 백엔드 계약을 "필드 존재 여부"로 명확히 유지

- `limits` 미포함: limit 정책 불변(usage/reset 상태 유지)
- `limits` 포함: 정책 교체(필요 시 재생성)

이 계약을 OpenAPI/스키마 주석과 서비스 단 테스트로 고정해 프론트 변경에도 의미가 흔들리지 않게 한다.

### 7.3 정책 교체는 명시적 UX로 분리

- "한도 정책 수정" 액션을 메타데이터 수정과 구분해 사용자 의도를 분명히 한다.
- 고위험 운영 환경에서는 교체 전에 확인 모달(usage reset 가능성 안내)을 추가한다.

### 7.4 회귀 테스트 보강

1. 이름만 수정한 PATCH -> `limits` 미전송, 기존 `current_value`/`reset_at` 유지
2. 활성 상태만 수정한 PATCH -> `limits` 미전송, usage 상태 유지
3. limit 값 실제 변경 PATCH -> `limits` 전송, 의도한 정책 교체만 발생
4. 순서만 다른 동일 rule set 제출 -> 변경 없음으로 판단(`limits` 미전송)

## 8) 기대 효과

- legacy 업그레이드 구간에서 인증 우회(fail-open) 제거
- 모델별 quota 정책의 의미를 정확히 보장
- API key 메타데이터 편집 시 quota usage가 의도치 않게 초기화되는 문제 제거
- 인증/인가/limit 계약이 분리되어 유지보수성과 테스트 가능성 향상

## 9) 추가 Review 요약 (API Key 편집/모델 목록 노출)

- 날짜: 2026-02-18
- 코멘트 1: `[P1] Preserve API key usage when updating non-limit fields`
- 위치: `app/modules/api_keys/service.py:182-185`
- 현상: `update_key()`에서 `limits_set=True`이면 `_limit_input_to_row()`로 limit row를 재생성하면서 `current_value=0`, 새 `reset_at`이 강제되어, 일반 편집(이름/활성화) 과정에서도 quota 사용량이 초기화될 수 있음.

- 코멘트 2: `[P2] Filter unsupported models from OpenAI-compatible model list`
- 위치: `app/modules/proxy/api.py:117-120`
- 현상: `/v1/models`, `/backend-api/codex/models`가 `supported_in_api=false` 모델까지 그대로 노출해, 클라이언트가 실제로는 사용 불가한 모델을 선택하게 만들 수 있음.

## 10) P1 문제 원인 (limit 재생성으로 usage 상태 유실)

`update_key()`는 `payload.limits_set`만 참이면 limit를 "교체(replace)"로 처리한다.  
현재 교체 경로는 기존 row의 상태(`current_value`, `reset_at`)를 이어받지 않고 신규 row를 생성하므로, 정책 자체는 동일해도 사용량 상태가 초기화된다.

## 11) P1 Best Practice 해결안

핵심 원칙: "정책 변경"과 "사용량 리셋"은 분리하고, 기본 동작은 항상 사용량 보존이다.

### 11.1 Limit 교체를 상태 보존형 Upsert로 전환

- 비교 키를 `(limit_type, limit_window, model_filter)`로 고정한다.
- 동일 키의 기존 rule이 있으면 `current_value`, `reset_at`을 유지한 채 `max_value`만 갱신한다.
- 새로 추가된 rule만 `current_value=0`으로 시작하고, 삭제된 rule만 제거한다.

### 11.2 사용량 초기화는 명시적 액션으로만 허용

- `reset_usage` 같은 명시 필드(기본 `false`) 또는 별도 "quota reset" 엔드포인트를 둔다.
- 일반 PATCH(메타데이터/정책 조정)는 usage 상태를 절대 초기화하지 않도록 계약을 고정한다.

### 11.3 프론트/백엔드 계약 동시 고정

- 프론트는 실제 limit 변경이 있을 때만 `limits`를 전송한다(7.1 원칙 유지).
- 백엔드는 `limits`가 오더라도 동일 정책이면 상태 보존으로 처리해 방어선을 이중화한다.

### 11.4 회귀 테스트 보강

1. 이름/활성 상태만 변경한 PATCH -> 기존 `current_value`/`reset_at` 유지
2. 동일 limit 정책 재전송 PATCH -> usage 상태 유지
3. `max_value` 조정 PATCH -> `current_value`/`reset_at` 유지, 임계치 판정만 변경
4. 명시적 reset 액션에서만 usage 초기화 발생

## 12) P2 문제 원인 (공개 모델 목록 필터 누락)

`_build_models_response()`가 `allowed_models`만 검사하고 `model.supported_in_api`를 필터링하지 않는다.  
결과적으로 registry snapshot에 있는 내부/실험 모델이 OpenAI 호환 모델 목록에 노출된다.

## 13) P2 Best Practice 해결안

핵심 원칙: "공개 가능 모델"의 단일 기준(`supported_in_api`)을 모든 모델 목록 엔드포인트에서 동일하게 적용한다.

### 13.1 공개 모델 필터를 단일 predicate로 통합

- 예: `is_public_model(model, allowed_models)` 헬퍼를 두고 아래 조건을 모두 만족할 때만 노출
- `model.supported_in_api is True`
- `allowed_models`가 설정된 경우 해당 집합에 포함

### 13.2 엔드포인트 간 계약 일관성 유지

- `/api/models`, `/v1/models`, `/backend-api/codex/models` 모두 동일 필터를 재사용한다.
- 특정 엔드포인트만 다른 기준을 쓰지 않도록 공통 함수/서비스 레이어로 고정한다.

### 13.3 회귀 테스트 보강

1. snapshot에 `supported_in_api=false` 모델 포함 시 `/v1/models` 응답에서 제외
2. 동일 조건에서 `/backend-api/codex/models` 응답에서도 제외
3. `allowed_models`에 포함돼도 `supported_in_api=false`면 노출되지 않음
4. `/api/models`와 OpenAI 호환 모델 목록 간 노출 집합 일치
