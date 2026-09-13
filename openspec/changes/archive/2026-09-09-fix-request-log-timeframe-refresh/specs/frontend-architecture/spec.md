## ADDED Requirements

### Requirement: Dashboard request-log filters use symbolic rolling timeframes

For `1h`, `24h`, and `7d`, the dashboard MUST send symbolic `timeframe` to
listing and options and MUST NOT send browser-generated `since`. Selecting
`all` MUST omit both. Refetches MUST preserve all applicable manual filters.

#### Scenario: Browser skew does not alter request-log filters

- **WHEN** the dashboard fetches or refetches `timeframe=24h`
- **THEN** both requests contain `timeframe=24h`
- **AND** neither contains `since`

### Requirement: Background request-log failure preserves retained rows

When a page loaded successfully and a later refresh fails, Request Logs MUST
keep the last successful filters, rows, total, and pagination visible. It MUST
also announce the current failure with a section-local alert and expose Retry.
Error-only rendering remains valid only before any page succeeds.

#### Scenario: Failed refresh retains table

- **GIVEN** a successful request-log page is visible
- **WHEN** a later refresh reaches terminal failure
- **THEN** the table and rows remain visible
- **AND** the section exposes alert and Retry
