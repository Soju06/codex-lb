## ADDED Requirements

### Requirement: Dashboard overview summary cards show previous-window usage deltas

The dashboard overview API SHALL expose previous-window comparison data for the existing `Requests`, `Tokens`, and `Est. API Cost` summary cards returned by `GET /api/dashboard/overview`. The comparison SHALL be tied to the selected overview timeframe so that `1d` compares the current 1-day window with the immediately preceding 1-day window, `7d` compares the current 7-day window with the immediately preceding 7-day window, and `30d` compares the current 30-day window with the immediately preceding 30-day window.

The overview response SHALL include a comparison block that exposes whether previous-window comparison is allowed and the previous-window totals for requests, tokens, and estimated API cost. The dashboard SHALL use that block to render a compact percentage-change indicator on the existing `Requests`, `Tokens`, and `Est. API Cost` cards only. The dashboard MUST NOT add this indicator to `Error rate` or `Account burn projection`.

If the immediately preceding window is not fully covered by eligible request-log history for the selected timeframe, the overview response SHALL mark the comparison as unavailable and the dashboard SHALL hide the percentage-change indicator for those cards.

If previous-window comparison is available and the previous total for a card is greater than zero, the dashboard SHALL calculate the displayed change from the current total relative to the previous total, SHALL show increases with an upward indicator using the project's positive `emerald` styling, and SHALL show decreases with a downward indicator using the project's negative `red` styling.

#### Scenario: Daily overview renders increase from previous window

- **WHEN** `GET /api/dashboard/overview?timeframe=1d` returns current totals for requests, tokens, and estimated API cost plus comparison data with `canCompare: true`
- **AND** the previous-window totals are lower than the current-window totals
- **THEN** the dashboard renders percentage-change indicators on the `Requests`, `Tokens`, and `Est. API Cost` cards
- **AND** each increase uses an upward indicator with positive `emerald` styling

#### Scenario: Weekly overview renders decrease from previous window

- **WHEN** `GET /api/dashboard/overview?timeframe=7d` returns comparison data with `canCompare: true`
- **AND** at least one of the previous-window totals for requests, tokens, or estimated API cost is higher than the current-window total for that same card
- **THEN** the dashboard renders a downward percentage-change indicator for that card
- **AND** that decrease uses negative `red` styling

#### Scenario: Partial previous window suppresses comparison

- **WHEN** `GET /api/dashboard/overview?timeframe=7d` or `GET /api/dashboard/overview?timeframe=30d` cannot prove the immediately preceding same-length window is fully covered by eligible request-log history
- **THEN** the overview response marks the comparison as unavailable
- **AND** the dashboard does not render percentage-change indicators on the `Requests`, `Tokens`, or `Est. API Cost` cards

#### Scenario: Non-comparison cards remain unchanged

- **WHEN** the dashboard renders overview cards from `GET /api/dashboard/overview` with or without comparison data
- **THEN** `Error rate` and `Account burn projection` do not render previous-window percentage-change indicators
