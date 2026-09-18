# Verification: dashboard locale review gaps

Verified locally on 2026-09-12 against the two findings in
[PR #2118 review 5184200558](https://github.com/Soju06/codex-lb/pull/2118#pullrequestreview-5184200558).
The baseline production bundle was built from `0bc0ccf73523134bbcab8e6db1a217a653bdf31a` before changing either component.

## Behavior and regression coverage

| Surface | Baseline | Fixed behavior and evidence |
| --- | --- | --- |
| Reports generation timestamp | English browser with Japanese selected rendered `9/6/2026, 2:30:45 PM 時点`. Both new Japanese component cases failed on the original implementation. | Shared formatter renders `午後02:30:45 2026/09/06 時点`; component coverage also checks 24-hour output, changing to ISO while mounted, and no label when the timestamp is absent. |
| API-key edit current usage | Browser rendered `Tokens (weekly, all)` and `Cost (monthly, all)`. | Existing translation keys produce Japanese type/window/all-model labels; component coverage switches the open dialog from English to Japanese and preserves model identifiers, 5h/7d, compact amounts, and USD values. |

The two browser regressions failed on the baseline labels and passed against the final production build. They use the real Reports route and API-key edit dialog with fixture APIs, browser locale `en-US`, dashboard language `ja`, timezone `Asia/Tokyo`, light theme, and a 1440 × 1000 viewport at 2× device scale.

## Validation

Commands run from `frontend/` unless specified otherwise; Node-based commands use `fnm exec --using=default`.

| Check | Result |
| --- | --- |
| Reports component tests | 28 passed, including three new cases. |
| API-key edit component tests | 21 passed, including the new language-switching regression. |
| Full frontend suite (`bun run test`) | 177 files and 1,575 tests passed. |
| `bun run lint` | Passed. |
| `bun run build` | Passed, including TypeScript checking. Existing large-chunk warning remains. |
| Playwright `--config screenshots/playwright.config.ts --grep 'Japanese locale review regressions'` | 2 passed against the final production build. |
| `openspec validate fix-dashboard-locale-review-gaps --strict` (root) | Passed. |
| `openspec validate --specs --strict` (root) | All 65 specifications passed. |
| `git diff --check` (root) | Passed. |

The implementation covers both reviewed requirements and their new scenarios,
follows the existing shared formatter/translation patterns, and adds no
dependencies or settings. The delta scenarios and stable context are synced to
the main frontend-architecture documents.

## Screenshots

All four captures were visually inspected. Reports captures the viewport; API-key edit captures the dialog after scrolling the current-usage section into view. Fixture account/model names and numeric values are not translated.

| Surface | Before | After |
| --- | --- | --- |
| Reports | [Before](screenshots/before-reports.jpg) | [After](screenshots/after-reports.jpg) |
| API-key edit | [Before](screenshots/before-api-key-edit.jpg) | [After](screenshots/after-api-key-edit.jpg) |

The capture cases live in `frontend/screenshots/capture.spec.ts`. To reproduce the final browser checks and screenshots, run:

```sh
fnm exec --using=default bun run playwright test --config screenshots/playwright.config.ts --grep 'Japanese locale review regressions'
```

Screenshots are emitted under `frontend/test-results/`. The baseline run used the same cases against the prebuilt baseline bundle, with `SCREENSHOT_WEBSERVER_COMMAND` set to preview only; assertions intentionally failed after screenshots were saved. This run reused the existing temporary Chromium installation through `PLAYWRIGHT_BROWSERS_PATH`.
