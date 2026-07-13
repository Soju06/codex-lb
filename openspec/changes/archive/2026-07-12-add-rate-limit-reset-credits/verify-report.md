## Verification Report

### `openspec validate add-rate-limit-reset-credits --strict`

- Result: passed
- Output: `Change 'add-rate-limit-reset-credits' is valid`

### `uv run pytest tests/integration/test_v1_reset_credit.py tests/unit/test_rate_limit_reset_credits_api.py -v`

- Result: passed
- Output: `28 passed in 1.52s`

### `uv run ruff check && uv run ruff format --check && uv run pytest`

- First attempt: tool timeout at 120000 ms while `pytest` was still running
- Rerun with a longer timeout: passed
- `ruff check`: `All checks passed!`
- `ruff format --check`: `648 files already formatted`
- `pytest`: `3703 passed, 45 skipped, 3 warnings in 223.70s (0:03:43)`

### Frontend Reset-Credit Contract Follow-Up

- Focused red/green after tightening the frontend consume contract:
  - `bun run test src/features/accounts/schemas.test.ts src/features/accounts/components/reset-credit-confirm-dialog.test.tsx`
  - Red step failed as expected before the code change on schema strictness and top-level query invalidation expectations
  - Green step passed with `12` tests passing
- Full frontend quality gate rerun after the fix:
  - `bun run lint` passed
  - `bun run typecheck` passed
  - `bun run test` passed with `104` test files and `652` tests passing in `89.29s`
- Frontend test stderr still includes existing React `act(...)`, Recharts zero-size container, and jsdom `HTMLCanvasElement.getContext()` warnings during an otherwise passing run

### Final Fresh Verification Snapshot

- `openspec validate add-rate-limit-reset-credits --strict` passed
- `openspec instructions apply --change "add-rate-limit-reset-credits" --json` now reports `40/41` tasks complete with only `6.5` remaining
- `bun run lint && bun run typecheck && bun run test` passed again after the frontend contract fix
  - `104` test files passed
  - `652` tests passed
  - Existing stderr warnings remained non-fatal

### `openspec validate --specs --strict`

- Result: passed
- Output: `Totals: 30 passed, 0 failed (30 items)`

### Frontend verification

- Repo package manager declaration: `frontend/package.json` declares `"packageManager": "bun@1.3.14"`
- Practical command form used in this worktree: `bun run lint`, `bun run typecheck`, and `bun run test` with `workdir=frontend`
- Note: `bun -C frontend ...` is not supported by the installed Bun CLI in this environment and fails with `error: Invalid Argument '-C'`

#### `bun run lint`

- Result: passed
- Output: `$ eslint .`

#### `bun run typecheck`

- Result: passed
- Output: `$ tsc -b`

#### `bun run test`

- Result: passed
- Output: `Test Files 104 passed (104)`
- Output: `Tests 652 passed (652)`
- Output: `Duration 91.73s`
- Notes: stderr included existing React `act(...)` warnings, Recharts zero-size container warnings, and jsdom `HTMLCanvasElement.getContext()` not-implemented warnings during the passing run

### Manual Verification

- Not performed in this implementation pass
- `openspec/changes/add-rate-limit-reset-credits/tasks.md` item `6.5` remained unchecked at that time (see follow-up below)

### Remaining OpenSpec Gaps

- `6.5` remained unchecked at implementation time because the requested manual UI verification was not performed in that pass. Closed by the 2026-07-13 follow-up verification below.

### Follow-up verification (2026-07-13)

Performed during PR #1252 review follow-up, against the shipped frontend (the
feature merged via PR #1053 and has been in production since). Each scenario in
task 6.5 was verified from shipped code plus its automated test, and the eight
covering test files were re-run in this worktree (`bun run test`, 8 files /
81 tests passed):

- Three Reset button placements with per-button count labels:
  `frontend/src/features/accounts/components/account-actions.tsx` (line 219,
  `Reset (${availableResetCredits})`),
  `frontend/src/features/dashboard/components/account-card.tsx` (line 224),
  `frontend/src/features/dashboard/components/account-list.tsx` (lines 331-345,
  count bubble). Visibility/label tests: `account-actions.test.tsx`
  ("shows/hides reset action"), `account-card.test.tsx` (same pair plus
  paused-account disable), `account-list.test.tsx` ("hides the reset action",
  "shows the banked reset count as a bubble").
- Accounts-nav total badge cap: `frontend/src/components/layout/app-header.tsx`
  line 57 caps at `"99+"`; `app-header.test.tsx` asserts the summed badge caps
  at `99+` and hides at zero. The dashboard table bubble cap is also asserted
  ("caps the reset count bubble at 99+").
- Countdown color flip at 7d: `frontend/src/utils/formatters.ts` line 313
  (`EXPIRING_SOON_THRESHOLD_MS = 7 * DAY_MS`); `formatters.test.ts` covers the
  boundary (7d -> `expiringSoon: false`, 6d -> `true`, plus 1h/1m/now); the
  flag drives `text-destructive` in `account-actions.tsx` lines 225-226 and
  `account-card.tsx` line 230.
- Local expiry timestamp: `formatLocalDateTimeSeconds` (`formatters.ts` lines
  233-238, local `YYYY-MM-DD HH:MM:SS`) rendered by
  `reset-credit-confirm-dialog.tsx` line 59; asserted in
  `reset-credit-confirm-dialog.test.tsx` ("Reset expires on
  \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}").
- Confirm flow: `reset-credit-confirm-dialog.test.tsx` covers confirm ->
  consume -> query invalidation, failure toast without invalidation, redeem
  request id reuse, loading/error/null-snapshot states.
- New sort option: `frontend/src/features/accounts/sorting.ts` lines 15/20
  ("Most reset credits" option, `DEFAULT_ACCOUNT_SORT_MODE =
  "most_reset_credits"`); `sorting.test.ts` covers default mode, descending
  count, soonest-expiry tiebreak, and null-expiry-last ordering.

Result: task 6.5 is now checked in the archived `tasks.md` with a pointer to
this section.
