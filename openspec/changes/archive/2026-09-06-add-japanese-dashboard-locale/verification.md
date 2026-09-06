# Verification: Japanese dashboard locale

Verified locally on 2026-09-06 using Bun, Node 24.20.0, Chromium, and OpenSpec
1.12.0. Browser checks use the existing fixture APIs and a production frontend
build, including Japanese browser settings and narrow mobile viewports.

## Assessment

| Dimension | Result |
| --- | --- |
| Completeness | All 7 implementation, integration, and documentation tasks completed; all 3 delta requirements implemented. |
| Correctness | Detection, selection, persistence, translation integrity, date preferences, calendar interaction, and API-key period labels verified. |
| Coherence | Existing i18next resources, detector, storage key, formatters, and installed calendar locale reused; no new dependencies or settings. |

No critical issues or change-specific warnings remain. The three delta
requirements match the main frontend-architecture specification. Stable context
and the existing Configuration page document the delivered behavior.

## Requirement evidence

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Dashboard supports runtime locale selection | `frontend/src/i18n/index.ts`, shared language toggle and existing mobile menu | Locale normalization tests; browser detection, query override, saved preference, reload, document language, and desktop/mobile switching checks. |
| Dashboard feature surfaces render in the active locale | Complete `ja.json` bundle; API-key table and detail period labels | All four bundles have 1,491 matching keys; interpolation variables and inline tags match English. Japanese plural, table/detail, login, validation, and inline-code rendering tests pass. |
| Japanese locale formats dashboard dates and calendar controls | Shared Intl formatter locale and API-key expiry picker | Japanese date/time and relative labels; explicit ISO/12h/24h preferences; unchanged compact units and USD amounts; accessible calendar navigation and date selection retaining 23:59:59 expiry. |

## Validation results

Commands below run in `frontend/` unless marked otherwise. Node-based commands
were run through `fnm exec --using=default`.

| Check | Result |
| --- | --- |
| `bun run test` | 152 files and 1,243 tests passed before the final API-key period-label refinement. |
| Final affected tests: i18n, formatters, expiry picker, API-key table, API-key details | 5 files and 63 tests passed, including all six tests added for period labels. |
| `bun run lint` | Passed after the final edits. |
| `bun run typecheck` / `bun run build` | Passed; the final production build also performs TypeScript checking. |
| `bunx playwright test --config screenshots/playwright.config.ts --grep 'Japanese locale'` | All 5 browser tests passed against the final production build. |
| `openspec validate add-japanese-dashboard-locale --strict` (repository root, before archive) | Passed. |
| `openspec validate frontend-architecture --type spec --strict` (repository root) | Passed. |
| `openspec validate --specs` (repository root) | All 58 specifications passed. |
| `git diff --check` (repository root) | Passed. |

The browser run used `SCREENSHOT_PORT=5180` and
`SCREENSHOT_BASE_URL=http://127.0.0.1:5180`. Chromium was installed in a temporary
Playwright browser directory. API navigation enters through the application
menu because Vite's existing `/api` proxy also matches direct `/apis` document
requests when the backend is absent.

Repository-wide strict validation returned 36 passed and 22 failed. Each failure
is an existing unrelated specification whose Purpose section contains a
placeholder; strict mode treats those warnings as failures. The changed
capability and change both pass strict validation. The unrelated specifications
were not changed.

## Visual verification

The screenshots use the same fixture data, light theme, and viewport sizes:
1440 × 1000 on desktop and 390 × 844 on mobile. Before images show English;
after images show Japanese. The Japanese dashboard and settings also passed
browser assertions against horizontal overflow at 1440 px and 390 px.

| Screen | Before | After |
| --- | --- | --- |
| Dashboard, desktop | [English](screenshots/before-dashboard-desktop.jpg) | [Japanese](screenshots/after-dashboard-desktop.jpg) |
| Dashboard, mobile | [English](screenshots/before-dashboard-mobile.jpg) | [Japanese](screenshots/after-dashboard-mobile.jpg) |
| Settings, desktop | [English](screenshots/before-settings-desktop.jpg) | [Japanese](screenshots/after-settings-desktop.jpg) |
| Settings, mobile | [English](screenshots/before-settings-mobile.jpg) | [Japanese](screenshots/after-settings-mobile.jpg) |

The mobile language menu, login screen, form errors, inline markup, Japanese
calendar weekdays/navigation, and API-key period labels were also checked in
the browser. Account names and identifiers in screenshots come from fixtures.
