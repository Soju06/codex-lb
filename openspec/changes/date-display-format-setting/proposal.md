## Why

Define the user-facing date display format preference and its effects on date/time rendering throughout the dashboard. The current display format is locale-dependent and cannot be overridden independently. Adding a date-format toggle under Appearance gives users control while keeping the default behavior unchanged.

## What Changes

- Add a "Date format" setting under the Appearance section with two options: **Default** (current behavior) and **ISO 8601** (`yyyy-mm-dd hh:mm:ss`).
- Store the preference in localStorage via a Zustand store (`codex-lb-date-display-format`).
- Apply the preference only to read-only date/time presentation text. In ISO 8601 mode, request logs and conversation tables render date on the top line (`yyyy-mm-dd`) and time on the bottom (`hh:mm:ss`) while keeping formatter field meanings stable.
- Do not apply the preference to interactive date/time controls (including inputs, calendars, and date pickers) or to verbatim API/data representations such as raw JSON, request/response payloads, copied values, and exports.
- Recharts visualizations (account trend, API trend, reports) are **not** affected by the date-format setting. They continue to use their existing x-axis formatting.
- Align the x-axis tick format of the Accounts and API trend charts to `MM-DD`, matching the reports chart convention.

## Capabilities

### New Capabilities

- `date-display-format`: User-facing date display format preference stored in localStorage, applied to read-only date/time presentation text across the dashboard except chart axes.

### Modified Capabilities

- `frontend-architecture`: The Appearance settings section SHALL include a Date format toggle with "Default" and "ISO 8601" options. The `formatTimeLong` formatter SHALL preserve the semantic meanings of its `time` and `date` fields in ISO 8601 mode, while rendered date surfaces SHALL place date before time. The Accounts and API trend chart x-axis SHALL format ticks as `MM-DD`.

## Impact

- `frontend/src/hooks/use-date-format.ts` (new): Zustand store
- `frontend/src/utils/formatters.ts`: `formatTimeLong` branching on date format and display-order helpers
- `frontend/src/features/settings/components/appearance-settings.tsx`: date format toggle UI
- `frontend/src/features/settings/components/appearance-settings.test.tsx`: tests
- `frontend/src/features/accounts/components/account-trend-chart.tsx`: x-axis tick format
- `frontend/src/features/apis/components/api-trend-chart.tsx`: x-axis tick format
- `frontend/src/features/quota-planner/components/quota-planner-section.tsx`: read-only decision Peak timestamp
- `frontend/src/i18n/locales/{en,zh-CN,ko}.json`: i18n labels
