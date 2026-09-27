# Verification: restore-account-detail-layout

## Completeness and correctness

All five tasks are complete and the two modified requirements are synchronized to frontend-architecture. The original layout is again available and defaults on when there is no valid saved preference. Existing List/Grid choices remain valid.

- `accounts-page.tsx` composes the original compact selector on the left and one inline AccountDetail on the right in Detail mode. The same AccountDetail supplies statistics, rendered trend charts, subscription information and permitted actions. Other modes retain their dialog. Selected account and filters survive mode switches.
- `account-list.tsx` reuses AccountListItem and the original bounded scroll region for Detail. The same mounted filter controls and overview pagination state survive all mode transitions. Overview cards/rows remain bounded at 24; Detail retains the full compact selector.
- Page and integration tests verify default inline detail, selection without dialogs, round trips to List/Grid, deep links, view persistence, aliases, pause/resume, force probe and reset confirmation.
- Browser checks verify actual trend chart rendering and side-by-side geometry at desktop widths, responsive document containment down to 390px, selected-account-only data requests in Detail, zero management fan-out in overviews, long selector scrolling, pagination, and targeted credit redemption. The compact selector count badge deliberately overhangs the row by four pixels; its parent contains this, and document overflow checks pass.

## Checks

- Focused Accounts page/list/integration and i18n tests: **31 passed in four files**.
- Browser scenarios: **five unique tests passed** (Detail, List, Grid, selection/pagination, credit redemption). The initial strict per-row overflow assertion was corrected to allow the original selector's intentional count-badge overhang, then Detail and the long-selector scenario passed.
- TypeScript, scoped ESLint, production Vite build and `git diff --check`: passed. Build output: `/tmp/codex-lb-account-detail-build`.
- Strict change validation: passed. Full spec validation: **51 passed / the same 15 baseline failures**, as documented in preceding changes. No new failing capability was introduced.

## Visual review

The restored `screenshots/detail-desktop.png` visibly shows the selector on the left and the statistics/chart panel on the right. Mobile stacks them vertically. Updated overview screenshots show the three choices. Before screenshots come from the preceding full-width List design. All screenshots use synthetic accounts. The restored desktop preview was sent through Telegram after visual inspection.

No new feature issue remains. Existing full-repository spec validation limitations are recorded above. No deployment, commit or push occurred.
