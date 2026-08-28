## ADDED Requirements

### Requirement: Reports API SHALL reject oversized daily ranges

`GET /api/reports` SHALL reject requests whose inclusive `start_date` to
`end_date` span exceeds 730 calendar days after applying endpoint defaults for
any omitted bound.

#### Scenario: Oversized report range is rejected

- **WHEN** an authenticated operator requests `/api/reports` with a date span
  longer than 730 days
- **THEN** the API returns a 400-class response
- **AND** the backend does not expand the request into per-day report buckets

#### Scenario: Single-bound report range is validated after defaults

- **WHEN** an authenticated operator requests `/api/reports` with only
  `start_date` set to a date more than 730 days before the effective end date
- **THEN** the API returns a 400-class response
- **AND** the backend does not expand the request into per-day report buckets
