## ADDED Requirements

### Requirement: Reports exposes a persisted line-chart visibility filter

The `/reports` page MUST expose a visible multi-select immediately before the
date controls. Its options MUST use the localized existing chart-header keys
`reports.charts.costByDay`, `reports.charts.tokensByDay`,
`reports.charts.timeToFirstToken`, `reports.charts.tokensPerSecond`, and
`reports.charts.queueWait`. The multi-select filter label MUST use the
`reports.filters.charts` key, and that key MUST be provided in each of
`en.json`, `ko.json`, and `zh-CN.json`. All five options MUST be selected by
default.
Selected line-chart cards MUST render, deselected line-chart cards MUST NOT
render, and an empty selection MUST be valid. Summary, donut, and table
sections MUST remain visible regardless of line-chart selection.

#### Scenario: Reports selects all line charts by default

- **WHEN** the `/reports` page loads without a saved visibility preference
- **THEN** the multi-select has all five chart options selected
- **AND** all five line-chart cards render
- **AND** the summary, donut, and table sections remain visible

#### Scenario: Reports renders a partial selection

- **GIVEN** the operator selects only Cost by Day and Queue Wait
- **WHEN** the Reports page renders
- **THEN** only those two line-chart cards render
- **AND** the other three line-chart cards do not render
- **AND** the summary, donut, and table sections remain visible

#### Scenario: Reports permits an empty selection

- **GIVEN** the operator deselects all five chart options
- **WHEN** the Reports page renders
- **THEN** no line-chart cards render
- **AND** the summary, donut, and table sections remain visible

#### Scenario: Reports provides the chart filter label in every required locale

- **WHEN** the frontend locale resources are checked
- **THEN** `en.json` provides a `reports.filters.charts` label
- **AND** `ko.json` provides a `reports.filters.charts` label
- **AND** `zh-CN.json` provides a `reports.filters.charts` label

### Requirement: Reports safely persists visibility

Reports MUST store the selected chart IDs as a JSON array under the exact
localStorage key `codex-lb-reports-visible-charts`. The only known chart IDs
MUST be the following five, in this canonical order: `costByDay`,
`tokensByDay`, `timeToFirstToken`, `tokensPerSecond`, `queueWait`. This
canonical order MUST be used for normalization, persistence, and rendering.
Missing storage MUST default to all five known chart IDs. A valid array MUST
be filtered to known IDs, deduplicated, and normalized to canonical chart
order; an empty array MUST remain empty. Malformed JSON, non-array values,
arrays containing any non-string values, and localStorage access failures MUST
default to all five known chart IDs. Storage failures MUST NOT disable
current-session in-memory visibility changes.

#### Scenario: Reports restores a persisted subset

- **GIVEN** localStorage contains a valid JSON array with the IDs for Tokens by
  Day and Queue Wait
- **WHEN** the `/reports` page initializes
- **THEN** those two chart options are selected
- **AND** their line-chart cards render
- **AND** the other three line-chart cards do not render

#### Scenario: Reports ignores unknown IDs and normalizes persisted values

- **GIVEN** localStorage contains a valid JSON array with known IDs in a
  non-canonical order, duplicate known IDs, and unknown IDs
- **WHEN** the `/reports` page initializes
- **THEN** unknown IDs are ignored
- **AND** duplicate IDs occur only once
- **AND** the selected IDs are normalized to canonical chart order

### Requirement: Visibility does not narrow data fetching

Changing chart visibility MUST NOT change Reports filter values, the TanStack
Query key, request parameters, response schema, or response parsing. Reports
MUST continue requesting the complete `GET /api/reports` payload and MUST NOT
send a chart-specific API parameter.

#### Scenario: Visibility changes leave the report query unchanged

- **GIVEN** Reports has loaded the complete `GET /api/reports` payload
- **WHEN** the operator changes the selected chart visibility
- **THEN** the Reports filter values remain unchanged
- **AND** the TanStack Query key and request parameters remain unchanged
- **AND** the complete report payload remains requested and parsed using the
  existing response schema
