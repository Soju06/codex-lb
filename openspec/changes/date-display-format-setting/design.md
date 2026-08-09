## Context

The dashboard currently formats dates using locale-dependent `Intl.DateTimeFormat` with no independent date-format override. Users on non-US locales get different date formats (e.g., `YYYY. MM. DD.` in ko-KR, `YYYY/MM/DD` in zh-CN) and cannot switch to a consistent ISO 8601 format across the UI. The request logs and conversation tables display time on top, date on bottom — which is inverted from ISO 8601 convention.

## Goals / Non-Goals

**Goals:**
- Add a "Date format" toggle in Appearance settings (Default / ISO 8601)
- Persist the choice to localStorage
- Apply ISO 8601 formatting to all date/time renderings except chart axes
- In ISO 8601 mode, swap date/time order in request log and conversation table cells (date top, time bottom)
- Align Accounts and API trend chart x-axis ticks to `MM-DD` (matching reports)

**Non-Goals:**
- Change chart data or chart axis behavior based on the date-format setting
- Add per-component override of the date format
- Support custom format strings beyond Default/ISO 8601
- Affect the existing locale-dependent `Intl.DateTimeFormat` when in Default mode

## Decisions

1. **Zustand store with localStorage persistence** — matches the existing pattern used by `useTimeFormatStore`, `useThemeStore`, and `useAccountQuotaDisplayStore`. Store key: `codex-lb-date-display-format`. Values: `"default"` | `"iso8601"`.

2. **Branch in `formatTimeLong` rather than per-component** — `formatTimeLong` is the bottleneck for all table-cell date rendering (request logs, conversation table). Adding the branch there means every consumer inherits ISO 8601 behavior without per-component changes. `formatDateTimeInline` calls `formatTimeLong` internally and also inherits the change.

3. **Swap date/time in ISO 8601 mode** — When active, `formatTimeLong` returns `{ time: "yyyy-mm-dd", date: "hh:mm:ss" }`. Since table cells render `time.time` on top and `time.date` on bottom, this naturally inverts the display: date on top, time on bottom. No JSX changes needed in consumer components.

4. **Chart x-axis alignment: `isoStr.slice(5, 10)`** — Replaces `toLocaleDateString(undefined, { month: "short", day: "numeric" })` in both `account-trend-chart.tsx` and `api-trend-chart.tsx` with a simple slice to produce `MM-DD`. This matches the reports convention (`d.date.slice(5)`) and is locale-independent. Chosen over `Intl.DateTimeFormat` to ensure consistency with reports and avoid locale variance.

5. **Recharts tooltips unaffected** — Tooltips in account/API trend charts use `formatChartDateTime()` which is not modified by this change (requirement 4.1).

## Risks / Trade-offs

- [Risk] ISO 8601 date/time swapped order could confuse users who expect time on top → Mitigation: The ISO 8601 label clearly describes the format; swapping is the standard ISO 8601 convention with date preceding time.
- [Risk] `slice(5, 10)` assumes ISO timestamp format `YYYY-MM-DDTHH:MM:SS...` → Mitigation: All trend data keys are ISO timestamps from the backend API; this is a stable contract.
