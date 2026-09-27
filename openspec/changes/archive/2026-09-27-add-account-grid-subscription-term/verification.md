# Verification: add-account-grid-subscription-term

## Implementation coverage

| Requirement | Implementation and evidence |
| --- | --- |
| Recorded subscription metadata | Optional claim validators in `app/core/auth/__init__.py`; summary mapping in `app/modules/accounts/mappers.py`; API import/list tests in `tests/integration/test_account_subscription.py` cover aware/naive dates, malformed and out-of-range values, missing data, elapsed periods, mismatched/free plans, and credential non-disclosure. |
| Remaining/unknown/elapsed presentation | `account-subscription.tsx` uses a single page-level minute timer, reports the recorded deadline and last check, and never changes account status. Component tests advance through the deadline and prove token expiry is not used. |
| Accounts grid | `account-list.tsx` shares existing filters/sorting and bounds cards to 24 per page. Cards reuse existing identity/privacy/status, quota and token components; page management callbacks are reused in the selected detail dialog. Integration coverage verifies filtering, view persistence, selection, pause/resume and returning to the filtered grid. |
| Responsive/no fan-out behavior | Playwright verifies desktop/mobile no-overflow, management dialog, reload persistence and zero trend/reset-credit requests when reloading a grid with closed details. Before/after screenshots are in `screenshots/`. |
| Compatibility | Frontend schema tests accept old summaries with no subscription field and naive UTC refresh dates, alongside explicit-offset refresh timestamps. No database migration or upstream billing request is introduced. |

## Checks

- Backend: `pytest -q tests/integration/test_account_subscription.py tests/unit/test_auth.py tests/unit/test_auth_refresh.py tests/integration/test_accounts_api.py` — 56 passed. Focused subscription API suite also passed all 12 cases.
- Frontend: `vitest run src/features/accounts/components src/features/accounts/schemas.test.ts src/__integration__/accounts-flow.test.tsx src/i18n/index.test.ts` — 17 files, 132 tests passed.
- Playwright `account-grid.spec.ts` — passed at desktop/mobile widths, including zero management-request fan-out after restoring grid view.
- TypeScript project check and production Vite build — passed; build output written outside the repo to `/tmp/codex-lb-account-grid-build`.
- Ruff and `ty check` for changed Python files — passed. ESLint for changed frontend files — passed. `git diff --check` — passed.
- Strict OpenSpec validation of this change and the new `account-subscription-term` capability — passed.

## Existing repository limitations

The full `ty check` has one diagnostic in an unrelated, previously modified test: `tests/integration/test_proxy_chat_completions.py:109` passes a dictionary where `ProxyResponseError` expects `OpenAIErrorEnvelope`. Changed Python files pass the focused check.

Full strict specification validation reported 50 passing / 15 failing capabilities before synchronizing this change, and 51 passing / the same 15 failing capabilities afterward. `frontend-architecture` has two existing requirements missing SHALL/MUST; validating a clean copy from HEAD reproduces both errors (requirement indices shift by one when the new grid requirement is added). This change's delta passes strict validation. The other failing capabilities are chat-completions-compat, compatibility-tooling, database-backends, model-catalog-compat, outbound-http-clients, proxy-admission-control, proxy-runtime-observability, query-caching, responses-api-compat, sticky-session-operations, telemetry, upstream-proxy-routing, usage-error-metrics and usage-refresh-policy.

No unverified feature behavior remains. These unrelated baseline failures were not modified. Deployment, commit and push were not performed.
