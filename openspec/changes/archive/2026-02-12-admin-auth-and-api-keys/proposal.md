## Why

codex-lb 대시보드와 프록시 엔드포인트가 기본적으로 무인증 상태로 노출된다. 현재 TOTP 인증만 존재하지만 환경변수(`CODEX_LB_DASHBOARD_SETUP_TOKEN`) 설정이 전제되어 있어 진입 장벽이 높고, password 계층이 없어 TOTP 단독으로는 완전한 인증 체계가 되지 않는다. 또한 프록시 API(`/v1/*`, `/backend-api/codex/*`)에 대한 접근 제어가 전혀 없어, 누구나 계정 풀을 통한 API 호출이 가능하다.

## What Changes

### 관리자 인증 (대시보드 세션)

- **Password 인증 계층 추가**: 설정 페이지에서 password를 최초 설정하면 이후 모든 대시보드 API 접근 시 세션 인증 필요
- **TOTP 설정 흐름 변경**: 기존 `X-Codex-LB-Setup-Token` 헤더 기반 → password 세션 기반으로 전환. 환경변수 의존성 제거
- **인증 미들웨어 통합**: 기존 TOTP 전용 미들웨어를 password + TOTP 통합 세션 미들웨어로 교체
- **기본값 비로그인**: password 미설정 시 기존과 동일하게 무인증 동작 (하위 호환)
- **세션 쿠키 확장**: 기존 `{exp, tv}` → `{exp, pw, tv}` (password 인증 + TOTP 검증 플래그)

### API Key 인증 (프록시 엔드포인트)

- **API Key CRUD**: 대시보드에서 API Key 생성/조회/수정/삭제/재생성
- **Bearer 토큰 인증**: API Key 인증 활성화 시 프록시 엔드포인트에 `Authorization: Bearer sk-clb-...` 필수
- **키별 정책 설정**: 허용 모델 목록, 주간 최대 토큰 사용량, 만료일
- **주간 사용량 추적**: 요청 완료 시 해당 키의 사용량 원자적 증가, 주간 자동 리셋
- **전역 스위치**: `api_key_auth_enabled` 설정으로 인증 활성화/비활성화. 비활성화 시 기존 무인증 동작 유지

### 인증 경계 **BREAKING**

- `/api/*` (대시보드 API): password 설정 시 세션 인증 필수. `/api/dashboard-auth/*`만 예외
- `/v1/*`, `/backend-api/codex/*`, `/api/codex/*` (프록시): API Key 활성화 시 Bearer 인증 필수
- `/health`: 항상 공개
- 정적 파일 (SPA): 항상 서빙하되, 프론트엔드에서 인증 상태에 따라 로그인 폼 게이트

### 프론트엔드

- `window.prompt()` TOTP 입력 → HTML 다이얼로그로 교체
- 로그인 화면 (password 입력 → 조건부 TOTP 입력)
- 설정 페이지: Password 관리 섹션, TOTP 관리 섹션 개선, API Key 관리 섹션 추가
- API Key 생성 다이얼로그: 이름, 허용 모델, 주간 한도, 만료일 설정. 생성 후 plain key 1회 표시

## Capabilities

### New Capabilities

- `admin-auth`: 관리자 password 인증 (설정/로그인/변경/제거), 세션 관리, password + TOTP 통합 인증 미들웨어
- `api-keys`: API Key 생명주기 (CRUD, 재생성), Bearer 인증 미들웨어, 키별 정책 (모델 제한/주간 한도/만료), 사용량 추적 및 주간 리셋

### Modified Capabilities

_(기존 spec에 요구사항 수준 변경 없음)_

## Impact

### 코드

- `app/core/middleware/dashboard_auth.py` → 통합 인증 미들웨어로 교체
- `app/modules/dashboard_auth/` → password 인증 엔드포인트 추가, TOTP 설정 흐름 변경
- `app/modules/settings/` → `api_key_auth_enabled` 필드 추가
- 신규 `app/modules/api_keys/` → API Key 관리 모듈 (api, service, repository, schemas)
- `app/modules/proxy/` → `request.state.api_key` 기반 모델 제한 검사
- `app/db/models.py` → `DashboardSettings` 확장 (`password_hash`, `api_key_auth_enabled`), `ApiKey` 모델 추가
- `app/db/migrations/versions/` → 마이그레이션 2건 추가
- `app/static/` → 로그인 UI, 설정 페이지 확장, API Key 관리 UI

### API

- 신규: `POST /api/dashboard-auth/password/setup`, `POST /api/dashboard-auth/password/login`, `POST /api/dashboard-auth/password/change`, `DELETE /api/dashboard-auth/password`
- 신규: `GET /api/api-keys`, `POST /api/api-keys`, `PATCH /api/api-keys/{id}`, `DELETE /api/api-keys/{id}`, `POST /api/api-keys/{id}/regenerate`
- 변경: `GET /api/dashboard-auth/session` → 응답에 `password_required` 필드 추가
- 변경: `PUT /api/settings` → `api_key_auth_enabled` 필드 추가
- **BREAKING**: TOTP 설정 엔드포인트에서 `X-Codex-LB-Setup-Token` 헤더 요구 제거 → 세션 인증으로 대체

### 의존성

- `bcrypt` 패키지 추가 (password 해싱)
- `pyotp` 패키지 추가 (TOTP 생성/검증) — 기존 자체 구현(`app/core/auth/totp.py`)을 표준 라이브러리로 교체

### 하위 호환

- password 미설정 + API Key 비활성화 = 기존과 동일한 무인증 동작
- 기존 TOTP가 설정된 환경: password 설정 후에도 TOTP는 독립적으로 동작 유지
