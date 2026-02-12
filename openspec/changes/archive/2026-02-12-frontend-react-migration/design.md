## Context

현재 프론트엔드는 `app/static/` 아래 단일 `index.html` + `index.js`(~3000줄) + `index.css` + 23개 컴포넌트 CSS로 구성된 Alpine.js SPA이다. FastAPI가 정적 파일로 서빙하며, Jinja2 템플릿 없이 클라이언트 사이드에서 모든 렌더링을 수행한다.

프론트엔드를 Vite + React + TypeScript + shadcn/ui 스택으로 전면 교체하며, 백엔드 API 응답의 over/under-fetching을 함께 정리한다. 레거시 호환은 불필요하다.

## Goals / Non-Goals

**Goals:**

- 기능별 모듈화된 React 프로젝트 구조로 유지보수성 확보
- TypeScript strict mode로 타입 안전성 확보
- shadcn/ui + Tailwind v4로 일관된 디자인 시스템
- TanStack Query로 서버 상태 캐싱·자동 갱신, 관심사별 독립 캐시
- Vite HMR로 개발 생산성 향상
- 기존 모든 기능의 1:1 동작 보존
- API 응답 over/under-fetching 해소 (미사용 필드 제거, pagination 메타데이터 추가)
- Zod 스키마로 API 응답 런타임 검증 — 백엔드 계약 변경 시 즉시 감지

**Non-Goals:**

- SSR/SSG 도입 (CSR SPA 유지)
- 국제화(i18n) 도입
- E2E 테스트 프레임워크 도입 (Playwright 등은 후속)

## Decisions

### D1. 프로젝트 위치: `frontend/` 루트 디렉토리

`app/static/` 내부가 아닌 프로젝트 루트에 `frontend/` 디렉토리를 생성한다. `vite build`의 `outDir`을 `../app/static`으로 설정하여 빌드 산출물만 기존 경로에 출력.

**대안**: `app/frontend/` 내부 배치. Python 패키지 구조와 혼재되어 관심사 분리가 불명확.

### D2. 빌드 도구: Vite

Vite는 ESBuild 기반 빠른 HMR, 네이티브 ES 모듈 지원, React/TypeScript 플러그인 내장. webpack 대비 설정이 간결하고 빌드 속도가 빠르다.

### D3. UI 컴포넌트: shadcn/ui (Tailwind v4 전용)

shadcn/ui 최신 버전은 Tailwind CSS v4만 지원한다. Radix UI + Tailwind 기반의 복사형 컴포넌트 라이브러리로, 의존성이 아닌 소스 코드로 관리되어 커스터마이징이 자유롭다. MUI/Ant Design 대비 번들 크기가 작다.

설치: `tailwindcss` + `@tailwindcss/vite` 플러그인. CSS 진입점에 `@import "tailwindcss";` 한 줄로 설정 완료 (tailwind.config.js 불필요). `npx shadcn@latest init`이 CSS 변수와 테마를 자동 구성한다.

기존 컴포넌트 → shadcn 매핑:

| 기존 | shadcn/ui |
|------|-----------|
| `.button` | `<Button>` |
| `.single-select` | `<Select>` |
| `.multi-select` | `<DropdownMenu>` + checkbox |
| `input`, `textarea` | `<Input>` |
| `checkbox` | `<Checkbox>` |
| `.dialog-backdrop` + `.window` | `<Dialog>` |
| `.message-dialog` | `<AlertDialog>` |
| `table` | `<Table>` |
| `.status-pill` | `<Badge>` variant |
| `progressbar` | `<Progress>` |
| `tabs` | `<Tabs>` (React Router 연동) |
| `tooltip` | `<Tooltip>` |
| `switch/toggle` | `<Switch>` |

### D4. 상태 관리: TanStack Query + zustand

| 상태 유형 | 도구 | 예시 |
|-----------|------|------|
| 서버 상태 (비동기) | TanStack Query | 계정 목록, 대시보드 데이터, 요청 로그, API Key, 설정 |
| URL 파생 상태 | React Router searchParams | 필터, 페이지네이션, 선택된 계정 ID |
| 폼 로컬 상태 | `useState` | 다이얼로그 입력, 로딩 플래그 |
| 전역 클라이언트 상태 | zustand | 인증 세션 (`useAuthStore`), 테마 (`useThemeStore`) |

**대안**: Redux Toolkit — 이 규모에서는 과도한 보일러플레이트. Context API — 리렌더링 최적화가 불충분.

### D5. 라우팅: React Router v6

3개 페이지를 라우트로 매핑:

```
/dashboard    → <DashboardPage>
/accounts     → <AccountsPage>
/settings     → <SettingsPage>
/             → redirect → /dashboard
```

FastAPI의 SPA fallback이 모든 경로에서 `index.html`을 서빙하도록 유지.

### D6. API 클라이언트: fetch 래퍼 + Zod 런타임 검증

`lib/api-client.ts`에 fetch 래퍼 구현:

- `get<T>(url, schema: ZodType<T>): Promise<T>` — 응답을 Zod 스키마로 parse하여 런타임 타입 검증
- `post<T>`, `patch<T>`, `del` 동일 패턴
- 401 응답 시 `useAuthStore` 상태 자동 갱신 (인터셉터)
- 에러 시 구조화된 `ApiError` throw
- Zod parse 실패 시 개발 모드에서 console.error로 불일치 즉시 감지 (프로덕션에서는 graceful fallback)

각 feature의 `api.ts`가 Zod 스키마를 정의하고, 이 래퍼를 통해 런타임 검증된 타입 안전한 응답을 반환한다. 백엔드 응답 필드 변경 시 프론트엔드에서 즉시 에러로 감지되어 휴먼에러를 방지한다.

### D7. 테마: Tailwind CSS dark mode

기존 CSS custom properties 기반 테마를 Tailwind의 `class` 전략 dark mode로 전환. `<html>` 태그에 `dark` 클래스 토글. shadcn/ui의 기본 테마 변수와 통합.

`useThemeStore`가 localStorage에 테마 저장하고, `<html>` 클래스를 동기화.

### D8. Feature-First Colocation

각 feature 디렉토리에 관련 코드를 모두 배치:

```
features/<name>/
├── components/     # React 컴포넌트
├── hooks/          # 커스텀 hooks (TanStack Query 래퍼)
├── api.ts          # API 함수 (Zod 스키마로 검증)
├── schemas.ts      # Zod 스키마 + z.infer<> 파생 타입 (단일 소스)
└── index.ts        # public export barrel
```

컴포넌트가 자신의 데이터 fetching hook을 직접 import → prop drilling 최소화. `types.ts` 대신 `schemas.ts`에서 Zod 스키마와 타입을 함께 관리하여 런타임 검증과 컴파일 타임 타입의 단일 소스를 유지한다.

### D9. 도넛 차트: CSS conic-gradient 유지

기존 conic-gradient 기반 도넛 차트를 React 컴포넌트로 래핑. D3/Chart.js 등 차트 라이브러리 도입 불필요 — 현재 구현이 충분히 경량이고 의존성 추가 없이 동작.

`components/donut-chart.tsx`에서 `buildDonutGradient()` 유틸리티 함수를 호출하여 인라인 스타일 생성.

### D10. 개발 워크플로우

- **개발**: `cd frontend && npm run dev` → Vite dev server (port 5173) + FastAPI 프록시
- **빌드**: `cd frontend && npm run build` → `app/static/`에 산출물 출력
- **Docker**: multi-stage build — Node.js stage에서 빌드 → Python stage에서 산출물 복사

### D11. FastAPI SPA 라우팅 조정

현재 `app/main.py`의 SPA 라우팅:
- `GET /` → redirect `/dashboard`
- `GET /accounts`, `GET /settings` → FileResponse(index.html)
- `app.mount("/dashboard", StaticFiles(...))` → Vite 빌드 결과에 맞게 조정

Vite는 `index.html`을 루트에 생성하므로, 모든 SPA 경로에서 `app/static/index.html`을 서빙하도록 catch-all fallback 패턴으로 통합.

### D12. Dashboard Overview 응답 분리

현재 `/api/dashboard/overview`가 accounts + summary + windows + request_logs를 한 번에 반환한다. React 전환 시 TanStack Query의 독립적 캐시 무효화를 활용하기 위해 request_logs를 분리한다:

- `GET /api/dashboard/overview` → accounts, summary, windows만 반환 (request_logs 필드 제거)
- `GET /api/request-logs` → request logs 전용 (기존 엔드포인트 그대로 사용)
- Dashboard 페이지에서 두 query를 병렬 호출, 각각 독립적으로 refetch/invalidate

이유: request logs 필터 변경 시 overview 전체를 refetch할 필요 없음. 30초 자동 갱신도 overview와 request logs를 독립적으로 수행 가능.

**대안**: overview에 request_logs 유지 — 필터 변경 시 overview까지 불필요한 refetch 발생, TanStack Query 캐시 활용 불가.

### D13. 응답 스키마 경량화 (BREAKING)

프론트엔드가 사용하지 않는 필드를 백엔드 응답에서 제거한다. 레거시 호환 불필요.

**AccountSummary 제거 필드:**
- `capacity_credits_primary`, `remaining_credits_primary` — usage percent로 충분
- `capacity_credits_secondary`, `remaining_credits_secondary` — 동일
- `last_refresh_at` — UI 미사용
- `deactivation_reason` — UI 미사용

**UsageHistoryItem 제거 필드:**
- `email` — accounts 배열과 중복

**UsageCost 제거 필드:**
- `by_model` — UI 미사용

**RequestLogsResponse 추가 필드:**
- `total: int` — 전체 결과 수
- `has_more: bool` — 다음 페이지 존재 여부

**RequestLogFilterOptionsResponse 추가 필드:**
- `statuses: list[str]` — 사용 가능한 status 값 목록 (ok, rate_limit, quota, error)

### D14. Zod 스키마 기반 API 응답 검증

각 feature의 `api.ts`에서 Zod 스키마를 정의하여 API 응답을 런타임 검증한다:

```typescript
// features/dashboard/schemas.ts
const DashboardOverviewSchema = z.object({
  accounts: z.array(AccountSummarySchema),
  summary: UsageSummarySchema,
  windows: DashboardUsageWindowsSchema,
});

// features/dashboard/api.ts
export const getDashboardOverview = () =>
  apiClient.get('/api/dashboard/overview', DashboardOverviewSchema);
```

TypeScript 타입은 `z.infer<typeof Schema>`로 자동 생성하여 스키마와 타입의 단일 소스를 유지한다. 별도 `types.ts`에 수동 타입 정의 대신 `schemas.ts`에서 Zod 스키마 + 파생 타입을 함께 관리한다.

### D15. 테스트 프레임워크: Vitest + React Testing Library + MSW

Vite 네이티브 테스트 러너인 Vitest를 사용한다. `vite.config.ts`를 공유하므로 path alias, 플러그인, 환경 변수 설정이 중복되지 않는다. Jest 대비 ESM/TypeScript 네이티브 지원, 4~10배 빠른 실행 속도.

| 도구 | 역할 |
|------|------|
| Vitest | 테스트 러너, 어설션, 모킹 (`vi.fn()`, `vi.mock()`) |
| jsdom | DOM 환경 시뮬레이션 |
| @testing-library/react | 컴포넌트 렌더링, 사용자 관점 쿼리 |
| @testing-library/user-event | 사용자 인터랙션 시뮬레이션 (click, type, keyboard) |
| @testing-library/jest-dom | DOM 매처 (`toBeInTheDocument()`, `toHaveTextContent()`) |
| MSW v2 | 네트워크 레벨 API 모킹 — TanStack Query와 투명하게 동작 |

**대안**: Jest — Vite 프로젝트에서 별도 `jest.config.ts` + 중복 transform 설정 필요, ESM 지원 불안정.

### D16. 테스트 구조: Feature-First Colocation

테스트 파일을 소스 파일 옆에 배치 (`.test.ts(x)`). feature 삭제 시 테스트도 함께 삭제. 공유 테스트 인프라는 `src/test/`에 중앙 관리.

```
src/
  test/                              # 공유 테스트 인프라
    setup.ts                         # Vitest setup (MSW, RTL cleanup, matchers)
    utils.tsx                        # renderWithProviders (QueryClient + Router 래핑)
    mocks/
      handlers.ts                    # MSW 기본 핸들러 (happy path)
      server.ts                      # MSW setupServer
      factories.ts                   # Zod 스키마 기반 테스트 데이터 팩토리

  features/dashboard/
    schemas.ts
    schemas.test.ts                  # Zod 스키마 유효성 테스트
    api.ts
    hooks/use-dashboard.ts
    hooks/use-dashboard.test.ts      # TanStack Query hook 테스트
    components/stats-grid.tsx
    components/stats-grid.test.tsx   # 컴포넌트 렌더링 테스트

  utils/
    formatters.ts
    formatters.test.ts               # 순수 함수 단위 테스트

  __integration__/                   # feature 간 통합 테스트
    auth-flow.test.tsx
    dashboard-flow.test.tsx
```

**대안**: 중앙 `tests/` 디렉토리 — 소스와 테스트가 분리되어 feature 삭제 시 고아 테스트 발생 위험.

### D17. 테스트 전략 및 커버리지 목표

3계층 테스트 전략:

| 계층 | 대상 | 도구 | 커버리지 목표 |
|------|------|------|--------------|
| **Unit** | Zod 스키마, 유틸 함수, 상수 | Vitest | 스키마 95%+, 유틸 90%+ |
| **Component** | 개별 컴포넌트 렌더링·인터랙션 | RTL + MSW | 70~80% |
| **Integration** | Feature 간 흐름 (인증 → 대시보드) | RTL + MSW + Router | 핵심 흐름 커버 |

**MSW 활용 패턴:**
- `handlers.ts`에 happy path 기본 핸들러 정의
- 개별 테스트에서 `server.use()`로 에러·엣지 케이스 오버라이드
- `afterEach`에서 `server.resetHandlers()`로 테스트 간 격리
- `onUnhandledRequest: 'error'`로 미처리 요청 즉시 실패

**TanStack Query 테스트 패턴:**
- 테스트마다 새 `QueryClient` 생성 (캐시 오염 방지)
- `renderWithProviders`가 `QueryClientProvider` + `BrowserRouter` 래핑
- `retry: false`, `gcTime: 0`으로 결정적 동작 보장
- `waitFor`로 비동기 상태 변화 검증 (인위적 타임아웃 금지)

**Zod 스키마 테스트:**
- `safeParse`로 유효/무효 입력 검증
- 필수 필드 누락, 타입 불일치, 기본값 적용, unknown 필드 제거 검증
- 백엔드 응답 계약의 방어선 역할 — 가장 높은 커버리지 목표

## Risks / Trade-offs

**[전면 재작성]** 점진적 마이그레이션이 아닌 전면 교체 → 기능 누락 위험. 기존 `index.js`의 모든 기능을 체크리스트로 추적하여 완화.

**[Node.js 빌드 의존성]** 프로덕션 런타임에는 불필요하나 CI/CD와 Docker 빌드에 Node.js 추가 필요. Multi-stage Docker build로 최종 이미지 크기 영향 없음.

**[shadcn/ui 커스터마이징]** 소스 복사 방식이므로 업스트림 업데이트를 수동 반영해야 함 → 현 규모에서는 관리 가능.

**[학습 곡선]** Alpine.js → React 전환 시 기여자 진입 장벽 → React가 더 넓은 생태계와 문서를 가져 장기적으로 유리.
