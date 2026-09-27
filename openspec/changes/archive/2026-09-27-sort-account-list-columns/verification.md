# Verification: sort-account-list-columns

## Summary

| Dimension | Result |
| --- | --- |
| Completeness | 6/6 tasks, 4 delta requirements implemented |
| Correctness | All new ordering and reset-count scenarios covered at the List surface |
| Coherence | Existing shared account sort state, metadata, compact layout and visibility settings retained |

## Coverage

- `sorting.ts` adds case-insensitive plan order, recorded subscription deadlines and independent 5h/7d remaining percentages in both directions. Numeric unknowns stay last, zero remains valid, monthly quota is never substituted, and the original reset/name/id tie breakers remain. Existing default is Most reset credits; the stale explicit-sort spec default was corrected to match it.
- `account-list.tsx` offers native keyboard-operable header buttons with arrow/direction labels and active state. Dropdown and headers update the same mode and reset pagination. Page-level selection fallback continues using the same shared comparator. The dropdown also works in standalone/uncontrolled AccountList use.
- `account-list-overview-row.tsx` uses existing summary reset counts and the existing badge visibility setting. Positive counts render as Reset (N); zero/missing counts omit the badge. The badge is informational and the row retains its detail-opening action. Token/request/purchased-credit metadata stays out of List.
- New `account-list-sorting.test.tsx` covers all four headers in both directions, keyboard toggling, all eight dropdown options, unknown plans/deadlines, unrelated access-token expiry, independent quota preference, zero and monthly-only quota, full-collection sorting before pagination, filter preservation, selected-row preservation and disabled reset badges. Row tests cover visible counts and zero/null/missing values.
- Browser List coverage exercises all four headers, both directions, actual order, keyboard activation, dropdown synchronization, mobile selection and List/Grid shared order. It verifies no per-row management requests, existing dialogs, all-row heights at most 80 px desktop/180 px narrow and no horizontal overflow at 1440, 1024, 768 and 390 px.

## Validation

- Targeted Vitest: 7 files, 52 tests passed (sorting controls, row, list, page, sorting helpers, real Accounts integration and locale parity).
- Playwright List browser test passed with new sort/badge assertions and screenshots.
- TypeScript build, scoped ESLint, production Vite build and git diff whitespace checks passed. Build output: `/tmp/codex-lb-list-sort-build`.
- Strict change validation passed.
- Full strict main-spec validation: 51 passed, 15 failed, matching the existing repository baseline. Failures remain in chat-completions-compat, compatibility-tooling, database-backends, frontend-architecture, model-catalog-compat, outbound-http-clients, proxy-admission-control, proxy-runtime-observability, query-caching, responses-api-compat, sticky-session-operations, telemetry, upstream-proxy-routing, usage-error-metrics and usage-refresh-policy. This change does not claim global spec validation is green.
- Synced three modified requirements and one added requirement into frontend-architecture; stable context records semantics, constraints and examples.

## Visual evidence and delivery

`screenshots/` contains before/after desktop and mobile List captures plus the mobile sort menu. Visual inspection confirmed compact rows, direction arrows and reset counts using synthetic account identities. Telegram received the desktop/mobile captures, message IDs 2089, 2090. Captions identify sample data and no deployment.

## Assessment

No change-specific critical issue or warning remains. Repository-wide spec failures are recorded above. Verified and ready for archive; no deployment, commit or push performed.
