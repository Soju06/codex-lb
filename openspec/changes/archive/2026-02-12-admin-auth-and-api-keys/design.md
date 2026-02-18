## Context

codex-lb는 현재 TOTP 전용 대시보드 인증만 제공하며, 프록시 엔드포인트는 완전 무인증이다. TOTP 설정에 환경변수(`CODEX_LB_DASHBOARD_SETUP_TOKEN`)가 필요하고, password 계층이 없어 TOTP만으로는 불완전한 인증이다.

현재 인증 관련 구조:
- **미들웨어**: `app/core/middleware/dashboard_auth.py` — `/api/*` 경로에서 TOTP 세션 쿠키만 검증
- **세션**: `DashboardSessionStore` — Fernet 암호화 쿠키 `{exp, tv}`, TTL 12h
- **모델**: `DashboardSettings` 싱글톤 — `totp_required_on_login`, `totp_secret_encrypted`, `totp_last_verified_step`
- **TOTP**: `app/core/auth/totp.py` — 자체 RFC 6238 구현 (HMAC-SHA1)
- **프록시**: `ProxyService._stream_once()` finally 블록에서 `RequestLog` 기록

## Goals / Non-Goals

**Goals:**

- Password → TOTP 순차 계층형 인증으로 대시보드 보호
- 기본값 비로그인 유지 (기존 사용자 무중단)
- API Key로 프록시 엔드포인트 접근 제어 (키별 모델·한도·만료 정책)
- 환경변수 기반 TOTP 설정을 세션 기반으로 전환
- 자체 TOTP 구현을 `pyotp` 라이브러리로 교체

**Non-Goals:**

- 다중 사용자/역할 기반 접근 제어 (RBAC) — 관리자 1명 모델 유지
- API Key별 요청 로그 분리 대시보드 — 기존 RequestLog에 api_key_id 참조만 추가
- API Key 자체에 대한 rate limiting (초당 요청 수 등) — 주간 토큰 한도만
- 프론트엔드 빌드 도구 도입 — 기존 vanilla JS + Alpine.js 유지

## Decisions

### D1. Password 해싱: bcrypt

bcrypt는 adaptive cost factor로 brute-force를 억제하며, `cryptography` 패키지가 이미 존재하므로 의존성 충돌이 없다. argon2는 더 현대적이나 추가 시스템 의존성이 필요하다. scrypt은 Python stdlib에 있지만 cost 파라미터 관리가 번거롭다.

- 패키지: `bcrypt` (pure Python wheel 제공)
- Cost factor: 기본값 12 (bcrypt 라이브러리 기본)
- `DashboardSettings.password_hash: str | None` — None이면 비로그인 모드

### D2. 세션 쿠키 확장

기존 Fernet 암호화 JSON 쿠키를 확장한다:

```json
{"exp": 1234567890, "pw": true, "tv": false}
```

- `pw`: password 인증 완료
- `tv`: TOTP 검증 완료 (기존)
- 기존 쿠키(`pw` 필드 없음)는 유효하지 않은 것으로 처리 → 재로그인 강제

`DashboardSessionStore.create()` 시그니처 변경: `create(*, password_verified: bool, totp_verified: bool) -> str`

### D3. 통합 인증 미들웨어 (단일)

기존 `add_dashboard_totp_middleware`를 `add_auth_middleware`로 교체. 하나의 미들웨어에서 경로 기반으로 세션/API Key 분기:

```
PUBLIC_PATHS = {"/health", "/api/dashboard-auth/"}
PROXY_PREFIXES = {"/v1/", "/backend-api/codex/", "/api/codex/"}
```

1. PUBLIC_PATHS → 통과
2. PROXY_PREFIXES → API Key 검증 (비활성화 시 통과)
3. `/api/*` → 세션 검증 (password 미설정 시 통과)
4. 나머지 → 통과 (정적 파일)

**대안**: 세션 미들웨어 + API Key 미들웨어 분리. 경로 판정 로직이 중복되고, 미들웨어 순서 의존성이 생기므로 단일이 더 간결하다.

### D4. API Key 형식 및 저장

- 형식: `sk-clb-{secrets.token_hex(24)}` → `sk-clb-` + 48자 hex = 총 55자
- DB 저장: `sha256(plain_key)` hex digest (64자)
- UI 식별: `key_prefix` = plain key 앞 15자 (예: `sk-clb-a1b2c3d4`)
- 생성 시 plain key 1회만 반환, 이후 조회 불가

### D5. API Key 테이블

```
api_keys:
  id              TEXT PK (uuid4)
  name            TEXT NOT NULL
  key_hash        TEXT NOT NULL UNIQUE (sha256 hex)
  key_prefix      TEXT NOT NULL (앞 15자)
  allowed_models  TEXT (JSON 배열, NULL = 전체 허용)
  weekly_token_limit   INTEGER NULL (NULL = 무제한)
  weekly_tokens_used   INTEGER NOT NULL DEFAULT 0
  weekly_reset_at      DATETIME NOT NULL
  expires_at      DATETIME NULL (NULL = 무기한)
  is_active       BOOLEAN NOT NULL DEFAULT TRUE
  created_at      DATETIME NOT NULL
  last_used_at    DATETIME NULL
```

인덱스: `idx_api_keys_hash` on `key_hash` (조회 성능)

### D6. 모델 제한 검사 위치: 서비스 레이어

미들웨어에서 request body를 파싱하면 스트리밍이 깨지므로 불가. 미들웨어는 `request.state.api_key`에 키 레코드를 저장하고, 프록시 API 핸들러(`proxy/api.py`)에서 모델명 비교 후 403 반환.

`/v1/models` GET 요청은 `allowed_models`가 설정된 경우 필터링된 목록만 반환.

### D7. 주간 토큰 리셋: Lazy on-read

별도 스케줄러 없이, API Key 검증 시 `weekly_reset_at < now()` 이면:
1. `weekly_tokens_used = 0`
2. `weekly_reset_at += 7 days` (반복하여 현재 시각 이후로 도달할 때까지)
3. 원자적 UPDATE (compare-and-swap)

**대안**: 백그라운드 스케줄러로 주기적 리셋. 불필요한 복잡성. 비활성 키도 리셋하게 되어 낭비.

### D8. 주간 토큰 사용량 기록: RequestLog 기록 시점

`ProxyService._stream_once()` finally 블록에서 `RequestLog` 기록할 때 동일 트랜잭션 내에서 `api_keys.weekly_tokens_used` 원자적 증가:

```sql
UPDATE api_keys
SET weekly_tokens_used = weekly_tokens_used + :tokens,
    last_used_at = :now
WHERE id = :key_id
```

토큰 수는 `input_tokens + output_tokens` 합산. 토큰 정보가 없는 경우(에러 등) 증가하지 않음.

### D9. TOTP 라이브러리 교체: pyotp

`app/core/auth/totp.py`의 내부 구현을 `pyotp`로 교체하되, 모듈의 공개 인터페이스(`generate_totp_secret`, `verify_totp_code`, `build_otpauth_uri`, `TotpVerificationResult`)는 유지한다. 호출하는 코드 변경 최소화.

- `generate_totp_secret()` → `pyotp.random_base32()`
- `verify_totp_code()` → `pyotp.TOTP(secret).verify(code, valid_window=window)` + 수동 step 비교
- `build_otpauth_uri()` → `pyotp.TOTP(secret).provisioning_uri(name, issuer_name)`

### D10. TOTP 설정 가드: 세션 기반

`X-Codex-LB-Setup-Token` 헤더 검증 제거. TOTP 설정/해제 엔드포인트는 통합 인증 미들웨어를 통과한 세션(password 인증 완료)으로 보호된다. 별도 가드 불필요.

`CODEX_LB_DASHBOARD_SETUP_TOKEN` 환경변수 및 관련 설정 필드 제거.

### D11. Settings 캐싱

인증 미들웨어는 **모든 요청마다** 실행되므로 매번 DB 조회는 비효율적. 인메모리 캐시 도입:

- `_cached_settings: DashboardSettings | None` + `_cached_at: float`
- TTL: 5초 (보안 설정 변경이 최대 5초 내 반영)
- 설정 변경 API(`PUT /api/settings`, password 설정 등) 호출 시 캐시 즉시 무효화

### D12. RequestLog에 API Key 참조 추가

`request_logs` 테이블에 `api_key_id TEXT NULL` 컬럼 추가. API Key 인증된 요청은 해당 키 ID를 기록. NULL이면 인증 없는 요청 또는 API Key 비활성화 상태에서의 요청.

## Risks / Trade-offs

**[단일 관리자 모델]** password가 하나뿐이므로 분실 시 복구 수단이 제한적 → SQLite DB 직접 수정(`password_hash = NULL`)으로 비로그인 모드 복원 가능. 복구 CLI 명령어는 향후 추가 가능.

**[Settings 캐싱 지연]** 최대 5초간 이전 설정 적용 가능 → 보안 설정 변경 시 캐시 즉시 무효화로 완화. 일반 운영에서 5초 지연은 허용 범위.

**[API Key 해시만 저장]** plain key 분실 시 복구 불가 → 재생성(regenerate) 엔드포인트 제공. 이는 의도된 보안 설계.

**[주간 리셋 lazy 방식]** 동시 요청 시 race condition → SQLite WAL 모드 + compare-and-swap UPDATE로 원자성 보장. 최악의 경우 1-2 토큰 오차 허용.

**[기존 TOTP 사용자 마이그레이션]** 이미 TOTP만 설정한 환경에서 업그레이드 시: password 미설정이므로 비로그인 모드. TOTP 상태는 DB에 보존되나, password를 설정하기 전까지 TOTP 검증 레이어가 비활성화됨. 사용자가 password 설정 후 TOTP를 재설정해야 할 수 있음.
