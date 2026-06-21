## ADDED Requirements

### Requirement: `/api/reports` returns nullable account buckets safely

`GET /api/reports` SHALL return an `accountId` field for each `byAccount` item that is either a string account identifier or `null`.
The system MUST preserve rows with `account_id IS NULL` and return them as a separate account bucket with `accountId: null` so historical usage is still represented.

#### Scenario: Null accountId is serialized for historical rows
- **WHEN** request logs in the selected period include rows with `account_id = NULL`
- **AND** those rows have non-null `cost_usd`
- **THEN** the `byAccount` response includes an item with `accountId: null`
- **AND** response serialization succeeds without schema validation failure

### Requirement: Reports data path uses backend-side date grouping

`GET /api/reports` SHALL use backend-side date grouping logic for both PostgreSQL and SQLite, producing `YYYY-MM-DD` daily buckets for stable trend display and CSV export.

#### Scenario: SQLite report request returns date buckets
- **WHEN** the repository is SQLite
- **AND** `/api/reports` is called with a valid date range
- **THEN** the response contains `daily` entries with `date` values in `YYYY-MM-DD` format
- **AND** the endpoint responds with HTTP 200

### Requirement: Reports API is accessible through the dashboard route map

The dashboard surface SHALL expose a reports page at route `/reports` and route to data loaded from `GET /api/reports` with `startDate`, `endDate`, `accountId`, and `model` filters.

#### Scenario: Dashboard reports page uses `/api/reports`
- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page loads the aggregated reports payload from `GET /api/reports`
- **AND** allows filtering by date range, model, and account
- **AND** uses the returned payload to render summary cards, daily charts, and model and user-agent distribution donuts

### Requirement: Reports distribution donuts show active-metric totals

The `/reports` page SHALL render both `Distribution by Model` and `Distribution by UserAgent` cards.
Each card SHALL show `Total` above the donut center value.
When the distribution metric toggle is `cost`, the center value and legend values SHALL show compact USD formatting with up to two decimal places and `K`, `M`, or `B` suffixes when applicable.
When the distribution metric toggle is `req`, the center value and legend values SHALL show compact request formatting with up to two decimal places and `K`, `M`, or `B` suffixes when applicable.

#### Scenario: Distribution donuts follow the active metric
- **WHEN** report data includes model and user-agent distribution rows
- **THEN** `/reports` renders both `Distribution by Model` and `Distribution by UserAgent`
- **AND** each donut center shows `Total` on one line and the active metric total on the next line
- **AND** switching a donut card from `cost` to `req` updates that card's center and legend values from compact USD totals to compact request totals
