## Why

프론트엔드가 단일 `index.js`(~3000줄) + Alpine.js로 구성되어 있어 기능 추가·디버깅·유지보수가 어렵다. 타입 안전성 없이 순수 JS로 작성되어 리팩토링 시 런타임 에러 위험이 높고, 빌드 도구가 없어 코드 분할·압축이 불가능하다. admin-auth-and-api-keys 등 복잡한 기능이 추가되면서 모놀리식 구조의 한계가 명확해졌다.

또한 기존 API 응답에 over-fetching(미사용 필드 전송)과 under-fetching(pagination 메타데이터 부재, 관심사 혼재)이 존재하여 프론트엔드 마이그레이션과 함께 정리한다.

## What Changes

### 백엔드 API 최적화 **BREAKING**

- **Dashboard overview 응답 분리**: `/api/dashboard/overview`에서 `request_logs` 필드 제거 — request logs는 기존 `/api/request-logs` 전용 엔드포인트만 사용
- **AccountSummary 경량화**: 미사용 필드 제거 (`capacity_credits_primary/secondary`, `remaining_credits_primary/secondary`, `last_refresh_at`, `deactivation_reason`)
- **UsageHistoryItem 정리**: 중복 `email` 필드 제거 (accounts 배열에서 이미 제공)
- **RequestLogs pagination 메타데이터 추가**: `RequestLogsResponse`에 `total`, `has_more` 필드 추가
- **RequestLogFilterOptions 확장**: `/api/request-logs/options`에 available status 목록 추가
- **비용 모델 분해 제거**: `UsageCost.by_model` 필드 제거 (UI 미사용)

### 프로젝트 구조

- **신규 `frontend/` 디렉토리**: Vite + React + TypeScript 프로젝트 생성
- **빌드 산출물**: `vite build` → `app/static/`로 출력하여 기존 FastAPI 정적 파일 서빙 유지
- **개발 모드**: Vite dev server + FastAPI 프록시 (`/api/*`, `/v1/*`, `/backend-api/*` → localhost:3000)
- **기존 `app/static/index.html`, `index.js`, `index.css`, `components/` 제거** **BREAKING**

### UI 프레임워크

- Alpine.js + 순수 JS → React 18 + TypeScript (strict mode)
- CSS custom properties + 수작업 컴포넌트 → Tailwind CSS v4 + shadcn/ui (Tailwind v4 전용)
- 상태 관리: Alpine.js 단일 객체 → TanStack Query (서버 상태) + zustand (클라이언트 상태)
- 라우팅: 수동 `pushState` → React Router v6

### 기능별 모듈화 (Feature-First Colocation)

- `features/auth/` — 로그인, 세션, TOTP 다이얼로그
- `features/dashboard/` — 메트릭, 도넛 차트, 계정 카드, 요청 로그 테이블
- `features/accounts/` — 계정 목록/상세, OAuth 플로우, 파일 임포트
- `features/settings/` — 라우팅 설정, Password/TOTP 관리
- `features/api-keys/` — API Key CRUD, 생성 결과 다이얼로그

### API 클라이언트

- 인라인 fetch 호출 → 타입 안전한 API 클라이언트 (`lib/api-client.ts`)
- API 응답을 Zod 스키마로 런타임 검증 → 백엔드 계약 변경 시 즉시 감지, 휴먼에러 방지
- API 응답 캐싱/자동 갱신: TanStack Query

### 테스트 아키텍처

- Vitest (Vite 네이티브 테스트 러너) + React Testing Library + MSW v2
- Feature-First Colocation: 소스 옆에 `.test.ts(x)` 배치
- Zod 스키마 계약 테스트로 백엔드-프론트엔드 계약 보호
- MSW 네트워크 레벨 모킹으로 TanStack Query 통합 테스트
- 커버리지 목표: 스키마 95%+, 유틸 90%+, 컴포넌트 70~80%, 전체 70%+ 최소선

### 유틸리티 보존

- 포매터 (숫자, 날짜, 토큰, 통화) → `utils/formatters.ts`로 이관 (순수 함수, 로직 동일)
- 상수 (STATUS_LABELS, ERROR_LABELS, DONUT_COLORS) → `utils/constants.ts`
- 도넛 그라디언트 계산 → `utils/colors.ts`

## Capabilities

### New Capabilities

- `frontend-architecture`: 프론트엔드 프로젝트 구조, 빌드 파이프라인, 개발 환경 설정, React 앱 진입점 및 라우팅, 공용 컴포넌트 라이브러리

### Modified Capabilities

- `dashboard`: 응답 스키마 변경 — overview에서 request_logs 분리, AccountSummary 경량화
- `request-logs`: pagination 메타데이터 추가, filter options에 status 목록 추가

## Impact

### 코드

- **제거**: `app/static/index.html`, `app/static/index.js`, `app/static/index.css`, `app/static/components/*.css` (23개 파일)
- **신규**: `frontend/` 디렉토리 전체 (Vite 프로젝트, ~50개 TSX/TS 파일)
- **수정**: `app/main.py` — SPA fallback 라우팅 조정 (빌드 산출물 경로)
- **수정**: `app/modules/dashboard/schemas.py` — overview 응답에서 request_logs 제거, AccountSummary 경량화
- **수정**: `app/modules/request_logs/schemas.py` — pagination 메타데이터 추가
- **수정**: `app/modules/accounts/schemas.py` — 미사용 필드 제거

### 의존성

- Node.js 런타임 (빌드 시에만 필요, 프로덕션 불필요)
- `package.json` (runtime): react, react-dom, react-router-dom, @tanstack/react-query, zustand, tailwindcss, @tailwindcss/vite, zod, lucide-react, vite, typescript
- `package.json` (devDependencies): vitest, jsdom, @testing-library/react, @testing-library/jest-dom, @testing-library/user-event, msw

### 하위 호환

- **BREAKING**: 백엔드 API 응답 스키마 변경 — 레거시 프론트엔드 호환 불필요
- 빌드 산출물이 `app/static/`에 출력되므로 FastAPI 서빙 구조 유지
- Docker 빌드 시 `npm run build` 단계 추가 필요
