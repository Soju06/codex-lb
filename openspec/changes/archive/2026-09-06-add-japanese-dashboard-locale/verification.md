# Verification: Japanese dashboard locale

Initially verified locally on 2026-09-06 using Bun, Node 24.20.0, Chromium, and OpenSpec
1.12.0. Browser checks use the existing fixture APIs and a production frontend
build, including Japanese browser settings and narrow mobile viewports.
The latest main sync and screenshot refresh are recorded in the 2026-09-12
section below.

## Assessment

| Dimension | Result |
| --- | --- |
| Completeness | All 7 implementation, integration, and documentation tasks completed; all 3 delta requirements implemented. |
| Correctness | Detection, selection, persistence, translation integrity, date preferences, calendar interaction, and API-key period labels verified. |
| Coherence | Existing i18next resources, detector, storage key, formatters, and installed calendar locale reused; no new dependencies or settings. |

The initial local checks passed, but CI later exposed a stale integration-test
expectation and missing contributor attribution; see the follow-up below. The
three delta requirements match the main frontend-architecture specification.
Stable context and the existing Configuration page document the delivered behavior.

## Requirement evidence

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Dashboard supports runtime locale selection | `frontend/src/i18n/index.ts`, shared language toggle and existing mobile menu | Locale normalization tests; browser detection, query override, saved preference, reload, document language, and desktop/mobile switching checks. |
| Dashboard feature surfaces render in the active locale | Complete `ja.json` bundle; API-key table and detail period labels | The refreshed bundles have 1,982 matching keys; interpolation variables and inline tags match English. Japanese plural, table/detail, login, validation, and inline-code rendering tests pass. |
| Japanese locale formats dashboard dates and calendar controls | Shared Intl formatter locale and API-key expiry picker | Japanese date/time and relative labels; explicit ISO/12h/24h preferences; unchanged compact units and USD amounts; accessible calendar navigation and date selection retaining 23:59:59 expiry. |

## Initial validation results: 2026-09-06

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

The eight screenshots below were refreshed on 2026-09-12 after merging main at
`bb4db9d08cc955743735678ccdb8d8bef19f7bea`. Each English/Japanese pair uses the
same fixture data, light theme, and viewport: 1440 × 1000 on desktop and
390 × 844 on mobile, captured at 2× device scale. Dashboard images capture the
viewport; Settings images capture the full page. Before images show English;
after images show Japanese. Both languages pass assertions against page-level
horizontal overflow at both widths.

The authenticated administrator belongs to a three-account fixture team.
Settings shows the current Access card with its People tab, a pending invite,
and the collapsed Organisation group. Account names, roles, and timestamps
come from fixtures. The service readiness response is also stubbed so these
frontend-only captures do not depend on a running backend.

| Screen | Before | After |
| --- | --- | --- |
| Dashboard, desktop | [English](screenshots/before-dashboard-desktop.jpg) | [Japanese](screenshots/after-dashboard-desktop.jpg) |
| Dashboard, mobile | [English](screenshots/before-dashboard-mobile.jpg) | [Japanese](screenshots/after-dashboard-mobile.jpg) |
| Settings, desktop | [English](screenshots/before-settings-desktop.jpg) | [Japanese](screenshots/after-settings-desktop.jpg) |
| Settings, mobile | [English](screenshots/before-settings-mobile.jpg) | [Japanese](screenshots/after-settings-mobile.jpg) |

The mobile language menu, login screen, form errors, inline markup, Japanese
calendar weekdays/navigation, and API-key period labels were also checked in
the browser. Account names and identifiers in screenshots come from fixtures.

## CI follow-up: 2026-09-07

PR #2118 reported one failure among 1,249 frontend tests: the settings API-key
list integration test still expected `Tokens: 125K/1M weekly`, while the
translated English period label renders `Tokens: 125K/1M Weekly`. The assertion
now matches that label. Runtime behavior is unchanged.

The contributor check also required `glyzinie` in `.all-contributorsrc`. Added
the GitHub profile with code, translation, and test contributions, and regenerated
the README contributor list using all-contributors-cli. Regeneration also renders
two contributors already present in the registry but absent from the old table.

Validation after these fixes:

- Full frontend suite: 152 files, 1,249 tests passed.
- Frontend lint and TypeScript checking: passed.
- Repository contributor checker with the current PR event and live GitHub
  contributor data: passed, covering 111 contributors.
- Simplicity budgets and `git diff --check`: passed.

These are local results; GitHub checks must run again after the fix is pushed.

## Main sync and screenshot refresh: 2026-09-11

Merged main through `a096b6092f7d43b5c59d823a4aefc1b08b6b7f6b`. The preceding
sync added 249 Japanese strings for the expanded access, organisation, auth,
overflow, and retention surfaces, bringing all four locale bundles to 1,889
keys. The two subsequent main commits only change backend code and its
specifications/tests; the frontend application and dependency files remain
unchanged from `914f81f9`.

Refreshed all eight English/Japanese screenshots because the original images
predate the account menu, Access card, and Organisation group. Their paths stay
the same. Added reproducible capture cases to
`frontend/screenshots/capture.spec.ts`, reusing the established capture helper
and the shared user/role factories. The original five Japanese browser checks
still run alongside the eight capture cases.

| Check | Result |
| --- | --- |
| Related backend tests: `test_db_migrate.py`, `test_http_bridge_event_batcher.py`, `test_bridge_ring_lifecycle.py`, and `test_api_keys_service.py` | 241 passed; nine SQLAlchemy reflection warnings. |
| `bun run lint` and `bun run build` | Passed; the production build includes TypeScript checking. |
| Playwright `--config screenshots/playwright.config.ts --grep 'Japanese locale'` | 13 passed: eight refreshed screenshots and five locale behavior checks. |
| `openspec validate --specs --strict` | All 65 specifications passed. The placeholder failures recorded in the initial verification above no longer occur. |
| `git diff --check` | Passed. |

The full frontend suite passed with 177 files and 1,571 tests during the
preceding same-day sync. Since its application and dependency files are
unchanged, this refresh reran the affected screenshot suite instead.

To regenerate the eight tracked images without running the other screenshot
scenes, run from `frontend/`:

```sh
bun run playwright test --config screenshots/playwright.config.ts --grep 'Japanese locale screenshots'
```

All eight regenerated images were visually inspected. The PR body originally
pinned image and verification links to `f27f8a2c`; publishing this refresh also
requires updating those links to the new commit after pushing it.

## Main sync and screenshot refresh: 2026-09-12

Merged 21 main commits through
`bb4db9d08cc955743735678ccdb8d8bef19f7bea` (1.25.0-beta.8) without conflicts.
The Reports timestamp and API-key edit-dialog fixes from `8ad30514` are
preserved. Added 93 Japanese strings for the thread identity report, cache
isolation probe, and password/emergency sign-in surfaces. All four locale
bundles now contain 1,982 keys, and Japanese interpolation variables and
inline tags match English. Existing English strings did not require Japanese
wording updates.

An independent semantic review of the 93 additions found two cache-probe
wording issues. The final translation explains that the prefix is unique per
run, rather than claiming that the seed sends it only once, and describes a
missing cache hit rather than asserting that nothing was stored.

Refreshed and visually inspected all eight tracked English/Japanese desktop
and mobile screenshots. Updated the Reports browser fixture for the new
thread-identity endpoint and confirmed that its card renders in Japanese
alongside the existing timestamp and API-key limit regressions.

| Check | Result |
| --- | --- |
| `bun run test` | 181 files, 1,643 tests passed. |
| Final locale and cache-probe component checks after wording refinement | 2 files, 19 tests passed. |
| `bun run lint` and `bun run build` | Passed after the final edits; the production build includes TypeScript checking. |
| Playwright `--config screenshots/playwright.config.ts --grep 'Japanese locale'` | 15 passed: eight refreshed screenshots, five locale behavior checks, and two review regressions. |
| Backend `make lint` | Passed, including Ruff and the migration topology guard: 258 revisions, one head, and no revision differences from `origin/main`. |
| Related backend tests | 188 passed: 187 before committing and the HEAD-dependent migration graph equality check after the merge commit. |
| `openspec validate --specs --strict` | All 65 specifications passed. |
| `mkdocs build --strict` | Passed. |
| `git diff --check` and `git diff --cached --check` | Passed. |

The pre-commit migration graph test reads revisions from `HEAD` and compares
them with the working tree. An unfinished merge necessarily differs by the
five migrations added by main; the topology guard itself passes against
`origin/main`. The equality check passed after the merge commit was created.
