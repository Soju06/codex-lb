## ADDED Requirements

### Requirement: Request logs attribute model-source overflow dispatches

The dashboard recent-requests table SHALL render a source chip on every row whose
request-log `source` is `subscription_overflow` or `subscription_overflow_pinned`,
visually distinguishing a fresh overflow from a pinned or anchored one, and the
request detail view SHALL show the source, the model source id and its kind for
those rows. The chip SHALL occupy the existing account cell (an overflow dispatch
has no account, so that cell otherwise reports only that the row is unassigned);
no request-log column is added, so stored column preferences keep their meaning.
Because the chip rides that cell rather than a column of its own, hiding the
**Account** column hides it too; the request detail view is therefore the surface
that MUST always carry the attribution. Rows with any other `source`, including
null, MUST render exactly as before.

The request-logs view SHALL offer a `source` filter over that closed set of two
values, sent as repeated `source` query parameters, and the dashboard SHALL render
neither the filter control nor the overflow tile unless
`GET /api/dashboard/overview` reports overflow activity, so an installation that
has never overflowed sees no new surface. A `source` selection carried in the URL
SHALL stay visible and clearable even when the control is otherwise hidden. The
filter's option values SHALL be client-side constants; the request-log filter
options endpoint MUST NOT gain a `source` facet. Labels SHALL exist in en, ko and
zh-CN.

#### Scenario: Fresh and pinned overflow rows are distinguishable

- **GIVEN** two request-log rows whose sources are `subscription_overflow` and
  `subscription_overflow_pinned`
- **WHEN** the recent-requests table renders them with the account column visible
- **THEN** each row shows a source chip with its own label and styling
- **AND** neither row shows the unassigned-account placeholder
- **AND** with the account column hidden the chip is hidden with it, while the
  request detail view still names the source, the model source id and its kind

#### Scenario: A row without an overflow source is unchanged

- **GIVEN** a request-log row whose source is null, `limit_warmup`, `warmup_probe`
  or an unrecognised value
- **WHEN** the recent-requests table and the request detail view render it
- **THEN** no source chip and no model-source block are shown
- **AND** the account cell renders the account label (privacy-blurred when it is an
  email and privacy mode is on) or the unassigned placeholder, as before

#### Scenario: Source filter round-trips through the URL

- **WHEN** the operator selects one or both overflow sources
- **THEN** the selection is written to the URL as repeated `source` parameters and
  sent to `GET /api/request-logs` as repeated `source` parameters
- **AND** it counts as an applied filter and is cleared by the filter reset
- **AND** the request-log filter options request carries no `source` parameter

#### Scenario: A default install shows no source filter and no overflow tile

- **GIVEN** an overview response whose summary reports no subscription-overflow
  activity, including a response from a backend that omits the field
- **WHEN** the dashboard renders
- **THEN** no source filter control and no overflow tile are present
- **AND** every other tile is unchanged

### Requirement: Dashboard surfaces subscription-overflow spend and live pins

When `GET /api/dashboard/overview` reports subscription-overflow activity, the
dashboard SHALL render one additional stat tile showing the `cost_usd` summed over
the two overflow `source` values within the overview's selected timeframe, the
number of dispatched requests in that window, and the number of live pinned
conversations. The tile SHALL state that a window without overflow is empty rather
than being hidden, so an operator who has switched overflow off can still see the
pins that are draining. Requests whose source reported no usage SHALL be counted
separately instead of being folded into the priced sum, because their cost is not
known to be zero. The tile is a breakdown of the existing estimated-cost tile, not
an addition to it: overflow rows are ordinary request-log rows and are already
counted there. Decline counts are Prometheus-only and MUST NOT be shown.

#### Scenario: Tile reports the window's overflow spend

- **GIVEN** overflow dispatches inside the overview's selected timeframe
- **WHEN** the dashboard renders
- **THEN** the tile shows the summed cost for that window, the dispatch count and
  the live pinned-conversation count
- **AND** the estimated-cost tile still reports the whole window's cost, overflow
  included

#### Scenario: Empty window with live pins keeps a neutral tile

- **GIVEN** an installation that has overflowed before, or holds live pins, but has
  no overflow dispatch inside the selected timeframe
- **WHEN** the dashboard renders
- **THEN** the tile shows a zero cost and states that the window contains no
  overflow, alongside the live pinned-conversation count

#### Scenario: Requests without reported usage are counted, not priced

- **GIVEN** an overflow dispatch whose source reported no usage
- **WHEN** the tile renders
- **THEN** the request is counted as being without reported usage
- **AND** it contributes nothing to the summed cost
