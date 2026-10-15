# Verification: Japanese locale parity after main synchronization

Addresses [PR #2118 comment 5662644441](https://github.com/Soju06/codex-lb/pull/2118#issuecomment-5662644441).
Verified locally on 2026-09-14 against main `d1fd2f21fa0e0f3b5fcad3af5fada19693cd1fc1`, merged into the existing Japanese branch at `431689d146baf14a077957c35a1d1d66c0df13d8`.

## Reproduction and repair

The unmodified merge reproduced both reported failures in `frontend/src/i18n/index.test.ts`: Japanese key parity failed, and the interpolation check threw on a missing value. English had 2,007 keys; Japanese had 1,982, with 78 missing and 53 obsolete keys.

The repair adds all 78 translations and removes all 53 obsolete keys. All four bundles (`en`, `ja`, `ko`, `zh-CN`) now have the same 2,007 keys. Retained English strings did not change during this main synchronization, and retained Japanese strings were preserved. A separate semantic review of the additions clarified the enabled-state label as `社内認証は有効です`.

## Validation

Commands run from `frontend/` unless marked as root. Screenshots use the production build and fixture APIs; no external identity provider is contacted.

| Check | Result |
| --- | --- |
| Existing locale integrity tests before repair | 2 failed / 11 passed, reproducing the reported issue. |
| `bun run test src/i18n/index.test.ts` after the final copy adjustment | 13 passed; exact keys, nonempty values, interpolation, and inline markup preserved. |
| `bun run test` | 181 files / 1,647 tests passed. |
| `bun run lint` | Passed. |
| `bun run build` | Passed, including TypeScript checks; the existing large-chunk advisory remains. |
| Japanese browser checks | 11 passed, including four new cases and the existing locale/review regressions. |
| Strict validation of this OpenSpec change (root) | Passed. |
| `openspec validate --specs --strict` (root) | 65 passed. |
| `uv run --no-cache --no-sync python scripts/check_migration_topology.py` (root) | Passed: 260 revisions, one head, no revisions added relative to main. |
| `git diff --check` (root) | Passed for the repair and the staged main merge. |

The full frontend suite ran after the key repair; the subsequent enabled-state wording adjustment was covered by another locale integrity run and a rebuilt production bundle.

## Browser evidence

The final browser run passed all 11 checks in 36.7 seconds. All ten comparison images were visually inspected: text wraps within the cards/dialogs, controls remain visible, and provider names and sign-in references retain their original values. Captures use the same fixture data, an English browser, light theme, Asia/Tokyo timezone, and a 1440 × 1000 viewport at 2× scale. `before` is English; `after` is Japanese on the same repaired build.

| Surface | Before | After |
| --- | --- | --- |
| OIDC connection | [English](screenshots/before-oidc-connection.jpg) | [Japanese](screenshots/after-oidc-connection.jpg) |
| OIDC claims | [English](screenshots/before-oidc-claims.jpg) | [Japanese](screenshots/after-oidc-claims.jpg) |
| Company sign-in failure | [English](screenshots/before-company-sign-in.jpg) | [Japanese](screenshots/after-company-sign-in.jpg) |
| Pending account | [English](screenshots/before-pending-approval.jpg) | [Japanese](screenshots/after-pending-approval.jpg) |
| Account rename | [English](screenshots/before-account-rename.jpg) | [Japanese](screenshots/after-account-rename.jpg) |

Reproduce after building:

```sh
PLAYWRIGHT_BROWSERS_PATH=/private/tmp/pr2118-playwright \
SCREENSHOT_PORT=4175 \
SCREENSHOT_WEBSERVER_COMMAND='bun run preview --host 127.0.0.1 --port 4175 --strictPort' \
fnm exec --using=default bun run playwright test \
  --config screenshots/playwright.config.ts \
  --grep 'Japanese locale' --grep-invert 'Japanese locale screenshots'
```

The temporary Chromium installation was prepared with `bun run playwright install chromium --only-shell` using the same browser path. Images are emitted under `frontend/test-results/`; the verified comparison files are retained here. The excluded older screenshot-only group would overwrite the original locale change's archived images; its behavioral checks run in the included groups.

## Implementation verification

The modified frontend requirement is implemented by the shared Japanese bundle and existing translation call sites. The locale gate covers added and removed keys, while browser checks exercise company sign-in, the pending-account explanation and reference, OIDC connection fields, and the account rename dialog. The implementation follows the existing bundle and fixture patterns and changes no application logic or settings.

Completeness, correctness, and coherence checks found no remaining issues. The updated requirement and stable context are synchronized to `openspec/specs/frontend-architecture/`.
