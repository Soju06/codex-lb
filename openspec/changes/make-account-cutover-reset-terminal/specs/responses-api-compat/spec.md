## ADDED Requirements

### Requirement: Account-assignment cutovers recover safely or terminate without retry
When an API-key account-assignment generation change makes a Responses continuity owner permanently unavailable, the service MUST first use its existing retained-request evidence to determine whether the request is a self-contained full resend. A verified self-contained full resend MUST be projected into a fresh account-neutral request and replayed at most once on a currently assigned eligible account. If safe reconstruction cannot be proven, the service MUST fail before unsafe upstream dispatch with HTTP 400, OpenAI error type `invalid_request_error`, code `continuity_reset_required`, parameter `previous_response_id`, and actionable guidance to start a new Codex conversation with `/new`. The terminal cutover response MUST NOT use a retryable 5xx status. Temporary owner unavailability outside an assignment cutover MUST retain the existing retryable continuity behavior.

#### Scenario: verified full resend rebuilds on a currently assigned account
- **GIVEN** an API-key assignment cutover removed the account that owns `previous_response_id`
- **AND** the request carries a verified self-contained full resend with retained prior assistant output
- **AND** the request has no account-scoped file, encrypted turn-state, or opaque conversation dependency
- **WHEN** the old owner cannot be selected
- **THEN** the service strips the old anchor and session-affinity headers
- **AND** replays the request at most once on a currently assigned eligible account
- **AND** does not overwrite the original sticky mapping as part of unsafe fallback

#### Scenario: incremental follow-up requires a new Codex conversation
- **GIVEN** an API-key assignment cutover removed the account that owns `previous_response_id`
- **AND** the request does not contain enough retained history to prove a safe fresh replay
- **WHEN** the service resolves the missing owner
- **THEN** it returns HTTP 400 with error type `invalid_request_error`
- **AND** the error code is `continuity_reset_required`
- **AND** the error parameter is `previous_response_id`
- **AND** the message instructs the operator to start a new Codex conversation with `/new`
- **AND** the request is not dispatched to a different upstream account

#### Scenario: unsafe account-scoped state is not replayed across accounts
- **GIVEN** a cutover request depends on an uploaded file, opaque conversation id, encrypted turn state, or orphan tool output
- **WHEN** the previous-response owner is no longer assigned
- **THEN** the service returns the terminal `continuity_reset_required` response
- **AND** it does not strip ownership evidence and replay the request on another account

#### Scenario: temporary owner failure remains retryable
- **GIVEN** no API-key account-assignment cutover is active
- **WHEN** a required previous-response owner is temporarily unavailable
- **THEN** the service preserves the existing retryable continuity response
- **AND** does not misclassify the failure as a permanent reset
