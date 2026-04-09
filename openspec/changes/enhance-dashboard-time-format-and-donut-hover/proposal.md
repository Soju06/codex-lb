## Why

Operators currently cannot choose whether dashboard timestamps render in 12-hour or 24-hour format, so the UI always reflects the browser default rather than an explicit dashboard preference. That makes time-heavy views such as request logs, sticky-session tables, and usage tooltips less predictable for teams that standardize on 24-hour time.

The dashboard donut charts also treat the pie and its HTML legend as disconnected surfaces. Hovering a legend item does not emphasize the matching slice, hovering a slice does not visually identify the matching legend row, and enlarging a slice would currently risk clipping against the chart container.

## What Changes

- Add an Appearance setting for `12h` or `24h` time display, defaulting to `12h`, and persist it locally for the dashboard UI.
- Make dashboard datetime rendering honor that configured time format across shared datetime surfaces and chart tooltips.
- Add donut-chart hover coordination so hovering either a slice or its legend row enlarges the matching slice and outlines the matching legend row.
- Slightly increase the donut-chart canvas so the active slice treatment does not clip inside its card.

## Impact

- Specs: `openspec/specs/frontend-architecture/spec.md`
- Frontend: appearance settings, shared datetime formatting utilities, dashboard donut chart interactions
- Tests: frontend unit coverage for persisted time-format behavior and donut hover interaction
