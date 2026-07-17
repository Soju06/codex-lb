## ADDED Requirements

### Requirement: Reports endpoint rejects inverted date ranges before repository work

After applying defaults for any omitted date bound, the Reports service MUST reject a `start_date` later than `end_date` before converting report boundaries or awaiting any repository operation. `GET /api/reports` MUST map that domain failure to HTTP 400 with the exact dashboard envelope `{"error":{"code":"invalid_report_date_range","message":"start_date must be on or before end_date"}}`. Valid one-day ranges and valid inclusive ranges of 730 calendar days MUST remain accepted.

#### Scenario: Explicit inverted Reports range is rejected

- **WHEN** an authenticated operator requests `GET /api/reports` with `start_date` later than `end_date`
- **THEN** the endpoint returns HTTP 400
- **AND** the response body is exactly `{"error":{"code":"invalid_report_date_range","message":"start_date must be on or before end_date"}}`
- **AND** the Reports repository receives no call

#### Scenario: Defaulted end date makes the range inverted

- **WHEN** an authenticated operator requests `GET /api/reports` with an explicit `start_date` later than the defaulted current `end_date`
- **THEN** the endpoint returns the same `invalid_report_date_range` HTTP 400 before repository work

#### Scenario: Boundary-valid Reports ranges remain accepted

- **WHEN** an authenticated operator requests a one-day range whose `start_date` equals `end_date`
- **THEN** the endpoint accepts the request and reports data for that day
- **WHEN** an authenticated operator requests an inclusive range of exactly 730 calendar days
- **THEN** the endpoint accepts the request under the existing range limit

### Requirement: Reports date controls prevent, explain, and recover from inverted ranges

The `/reports` start-date input MUST use the earlier of the browser-local current day and a present end date as its native `max`, and the end-date input MUST use a present start date as its native `min` while retaining the browser-local current day as its `max`. If both values are present and the start date is later than the end date, both controls MUST expose `aria-invalid`, both MUST reference the same localized inline corrective message through an accessible description, and neither the filtered Reports query nor the relaxed Reports filter-catalog query MAY send a request. Correcting either bound so the range is ordered MUST clear the invalid state and resume each distinct Reports query with the corrected bounds.

#### Scenario: Reciprocal native bounds prevent routine inverted selection

- **GIVEN** `/reports` has a selected start date and end date
- **THEN** the start-date control's `max` is the earlier of the end date and the browser-local current day
- **AND** the end-date control's `min` is the start date
- **AND** the end-date control's `max` remains the browser-local current day

#### Scenario: Bypassed inverted input is accessible and sends no Reports request

- **WHEN** typed, restored, or programmatically supplied Reports dates have a start date later than the end date
- **THEN** both date controls expose `aria-invalid`
- **AND** both controls reference one visible localized message that tells the operator to place the start date on or before the end date
- **AND** no `GET /api/reports` request is sent for either Reports query

#### Scenario: Retry while inverted only retries Accounts

- **GIVEN** `/reports` has an inverted date range and loading account options failed
- **WHEN** the operator activates the page-level Retry action
- **THEN** the Accounts query sends a retry request
- **AND** neither Reports query sends a request

#### Scenario: Correcting either invalid bound resumes Reports queries

- **GIVEN** `/reports` has an inverted date range and both Reports queries are disabled
- **WHEN** the operator corrects either date bound so the start date is on or before the end date
- **THEN** both controls clear the invalid state and accessible description
- **AND** the corrective message is removed
- **AND** each distinct Reports query sends one request using the corrected ordered bounds
