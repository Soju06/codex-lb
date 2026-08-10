## Context

The dashboard currently formats dates using locale-dependent `Intl.DateTimeFormat` with no independent date-format override. Users on non-US locales get different date formats (e.g., `YYYY. MM. DD.` in ko-KR, `YYYY/MM/DD` in zh-CN) and cannot switch to a consistent ISO 8601 format across the UI. The request logs and conversation tables display time on top, date on bottom. ISO 8601 mode should display the calendar date first while preserving the semantic meaning of formatter fields for status bars and date-first inline surfaces.

## Goals / Non-Goals

**Goals:**
- Add a "Date format" toggle in Appearance settings (Default / ISO 8601)
- Persist the choice to localStorage
- Apply ISO 8601 formatting to read-only date/time presentation text except chart axes
- In ISO 8601 mode, render date before time in request log and conversation table cells while keeping formatter field meanings stable
- Align Accounts and API trend chart x-axis ticks to `MM-DD` (matching reports)

**Non-Goals:**
- Change chart data or chart axis behavior based on the date-format setting
- Change date/time formatting inside interactive controls used to enter, edit, select, or filter values, including native inputs, calendars, and date pickers
- Rewrite verbatim API/data representations, including raw JSON, request/response payloads, metadata, copied values, filenames, and exports
- Add per-component override of the date format
- Support custom format strings beyond Default/ISO 8601
- Affect the existing locale-dependent `Intl.DateTimeFormat` when in Default mode

## Decisions

1. **Zustand store with localStorage persistence** — matches the existing pattern used by `useTimeFormatStore`, `useThemeStore`, and `useAccountQuotaDisplayStore`. Store key: `codex-lb-date-display-format`. Values: `"default"` | `"iso8601"`.

2. **Centralize formatting while passing the subscribed preference** — `formatTimeLong` is the common formatter for table-cell date rendering (request logs, conversation table) and keeps the ISO field construction in one place. Rendered date surfaces subscribe to the preference and pass it to the formatter helpers, so mounted components update immediately. `formatDateTimeInline` calls the same display-order helper.

3. **Keep fields semantic and order display values separately** — `formatTimeLong` always returns the clock value in `time` and the calendar value in `date`. A display-order helper returns time then date in Default mode and date then time in ISO 8601 mode for stacked and inline surfaces. This keeps the status bar's `lastSync.time` correct and preserves existing date-first concatenation callers.

4. **Chart x-axis alignment: `isoStr.slice(5, 10)`** — Replaces `toLocaleDateString(undefined, { month: "short", day: "numeric" })` in both `account-trend-chart.tsx` and `api-trend-chart.tsx` with a simple slice to produce `MM-DD`. This matches the reports convention (`d.date.slice(5)`) and is locale-independent. Chosen over `Intl.DateTimeFormat` to ensure consistency with reports and avoid locale variance.

5. **Recharts tooltips unaffected** — Tooltips in account/API trend charts use `formatChartDateTime()` which is not modified by this change (requirement 4.1).

6. **Presentation-only scope** — The preference controls read-only text that presents a date or timestamp to the user, such as table cells, detail fields, status text, and informational labels. Interactive controls retain the format required by their component or browser so their input and selection semantics remain stable. Verbatim API/data representations remain unchanged so displayed, copied, downloaded, or exported data preserves its source format. For example, an API-key expiry shown in a read-only table follows the preference, while the expiry date picker and a raw JSON `expires_at` value do not.

## Risks / Trade-offs

- [Risk] ISO 8601 date-first order could confuse users who expect time on top → Mitigation: The ISO 8601 label clearly describes the format and only the rendered order changes; formatter field semantics remain stable.
- [Risk] `slice(5, 10)` assumes ISO timestamp format `YYYY-MM-DDTHH:MM:SS...` → Mitigation: All trend data keys are ISO timestamps from the backend API; this is a stable contract.
