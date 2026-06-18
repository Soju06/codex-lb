## Why

The reports dashboard surface currently contains non-English page-owned wording. Operators need `/reports` to present English UI labels consistently while continuing to load report data from `GET /api/reports`, keep visible filter controls for start date, end date, account, and model, and keep the request parameter names `startDate`, `endDate`, `accountId`, and `model`.

Additionally, recharts v3.8.1 changed `TooltipProps` to omit context-injected properties (`payload`, `label`, `active`, `coordinate`) via `Omit<..., PropertiesReadFromContext>`. The shared `ChartTooltip` component must use `TooltipContentProps` (which includes those properties) wrapped in `Partial` so the JSX call site compiles while the component body retains full typing.

## What Changes

- Update page-owned user-facing labels on `/reports` to English wording.
- Use an explicit accepted English wording set for the current `/reports` page-owned labels, including the page title, subtitle, loading label, summary labels, chart titles, daily table title, daily table headings, and page-owned error/retry labels.
- Keep `/reports` loading and refetching report data from `GET /api/reports`.
- Keep `/reports` exposing visible filter controls for start date, end date, account, and model.
- Keep `/api/reports` requests from `/reports` using the parameter names `startDate`, `endDate`, `accountId`, and `model`.
- Keep backend-provided strings, account/model values, and raw server error payload text out of scope unless `/reports` wraps them with page-owned labels.
- Fix `ChartTooltip` type to use `Partial<TooltipContentProps>` from recharts instead of `TooltipProps` so recharts v3.8.1 builds cleanly.
- Verify `/reports` page-owned headings, controls, summaries, charts, and page-owned loading/empty/error labels render English user-facing text.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: `/reports` wording is clarified as concrete accepted English labels for the current page-owned reports surface plus separate contracts for `GET /api/reports` loading/refetch, visible filter controls, and preserved request parameter names, with backend-provided strings and raw payload text remaining out of scope unless wrapped by page-owned labels. `ChartTooltip` typing is clarified to use `Partial<TooltipContentProps>` for recharts v3.8.1 compatibility.

## Impact

- Frontend: reports page text on `/reports` and any directly rendered reports-page labels; `ChartTooltip` type definition in `chart-tooltip.tsx`.
- Specs: `frontend-architecture` delta for reports-page wording and chart tooltip typing.
- Verification: confirm `/reports` renders the accepted English page-owned wording set, still loads/refetches through `GET /api/reports`, still exposes visible filter controls for start date, end date, account, and model, still uses request parameter names `startDate`, `endDate`, `accountId`, and `model`, and `tsc -b` passes cleanly.
