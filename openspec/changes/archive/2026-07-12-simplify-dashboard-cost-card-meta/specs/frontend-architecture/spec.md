## ADDED Requirements

### Requirement: Dashboard estimated cost card meta avoids duplicate estimate and cache copy

The dashboard overview `Est. API Cost` summary card SHALL render its meta text as only the averaged cost for the selected overview timeframe. The meta text MUST NOT append duplicate estimate wording or cached-token counts.

#### Scenario: Weekly estimated cost card shows only average-per-day text

- **WHEN** `GET /api/dashboard/overview?timeframe=7d` returns an `Est. API Cost` total and the summary metrics also include cached input tokens
- **THEN** the dashboard renders the cost-card meta text as `Avg/day <currency value>`
- **AND** the same meta text does not include `API estimate`
- **AND** the same meta text does not include `cached`

#### Scenario: Daily estimated cost card shows only average-per-hour text

- **WHEN** `GET /api/dashboard/overview?timeframe=1d` returns an `Est. API Cost` total
- **THEN** the dashboard renders the cost-card meta text as `Avg/hr <currency value>`
- **AND** the same meta text does not include any extra suffix text
