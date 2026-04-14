## ADDED Requirements

### Requirement: Request log error previews remain recognizable in the dashboard

The Dashboard recent requests table MUST show a compact but recognizable preview for request-log errors without relying on a single-line truncation that hides the nature of the failure. For rows with an error code or error message, the table MUST expose a visible detail affordance directly in the row.

#### Scenario: Error row shows a recognizable preview

- **WHEN** `/api/request-logs` returns a row with a non-empty `errorMessage`
- **THEN** the recent requests table shows a compact preview that preserves enough text for the operator to recognize the failure category
- **AND** the row includes a visible action that opens full request details

#### Scenario: Error row without message still exposes details

- **WHEN** `/api/request-logs` returns a row with `errorMessage = null` and a non-empty `errorCode`
- **THEN** the recent requests table shows the error code as the preview
- **AND** the row still includes the visible request-details action

### Requirement: Request log details expose full failure context

The dashboard MUST provide a request-details surface for request-log rows so operators can inspect full failure context without losing the surrounding request-log state. For failed rows, that surface MUST display the full request id, status, model, transport, error code, and full error message when present.

#### Scenario: Operator opens request details for a failed row

- **WHEN** the operator opens request details for a request-log row whose status is not `ok`
- **THEN** the dashboard shows a request-details surface containing the row's full request id, status, model, transport, error code, and full error message
- **AND** the full error text is visible without truncation

#### Scenario: Opening details preserves table context

- **WHEN** the operator opens and closes a request-details surface from the recent requests table
- **THEN** the dashboard preserves the current request-log filters, pagination, and scroll context

### Requirement: Request log details support copy-oriented debugging workflow

The request-details surface MUST support copying the most useful debugging identifiers directly from the UI. At minimum, it MUST provide copy actions for the request id and the full error text when an error is present.

#### Scenario: Operator copies request id from request details

- **WHEN** the operator activates the request id copy action in the request-details surface
- **THEN** the dashboard copies the full request id value without truncation or formatting changes

#### Scenario: Operator copies full error text from request details

- **WHEN** the operator activates the error copy action for a failed request row
- **THEN** the dashboard copies the full error text exactly as shown in the request-details surface
