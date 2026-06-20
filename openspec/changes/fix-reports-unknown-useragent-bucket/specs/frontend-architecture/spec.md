## ADDED Requirements

### Requirement: Reports user-agent distribution preserves unknown buckets

`GET /api/reports` SHALL aggregate request-log rows whose normalized `request_logs.useragent_group` is `null` into a `byUseragent` bucket labeled `Unknown`. When `/reports` or `GET /api/reports` is filtered with `useragent_group=Unknown`, the system SHALL match those same null-backed rows. The `/reports` `Distribution by UserAgent` card SHALL render the `Unknown` bucket with a fixed gray legend marker and slice color instead of a rotated palette color.

#### Scenario: Reports payload includes unknown user-agent traffic

- **WHEN** `GET /api/reports` aggregates request logs that include one or more rows with `request_logs.useragent_group = null`
- **THEN** the response `byUseragent` array includes an entry with `useragent: "Unknown"`
- **AND** that entry aggregates the null-backed rows' request counts and costs

#### Scenario: Reports filter matches unknown user-agent traffic

- **WHEN** `/reports` or `GET /api/reports` requests `useragent_group=Unknown`
- **THEN** the returned report aggregates include only rows whose normalized `request_logs.useragent_group` is `null`

#### Scenario: Reports page renders the unknown user-agent bucket with fixed gray styling

- **WHEN** `/reports` renders `Distribution by UserAgent` data that includes `useragent: "Unknown"`
- **THEN** the `Unknown` legend dot uses a fixed gray color
- **AND** the matching donut slice uses that same fixed gray color
