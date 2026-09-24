## ADDED Requirements

### Requirement: Stored asynchronous pairs retain validation evidence after settlement

Before either durable full-resend proof accepts asynchronous history, the proxy MUST validate every async call and matching typed output in the stored prefix using the existing self-contained tool-item rules, even when the pair is already settled. Removing a call from outstanding async state MUST NOT discard its validation evidence. Synchronous prefix validation and account-ownership requirements MUST remain unchanged.

#### Scenario: Settled async work spans an intervening turn

- **GIVEN** a stored prefix contains an async function or custom tool call, an intervening user turn, and its matching output
- **WHEN** either durable full-resend proof classifies a continuation
- **THEN** malformed call bodies, blank identities, missing output values, unsupported fields or non-self-contained callers MUST reject that proof
- **AND** valid settled pairs and delayed suffix results MUST remain admissible under the existing boundary and ownership checks

#### Scenario: Invalid prefix cannot authorize owner-bound unanchored reattachment

- **GIVEN** a reconnecting HTTP client supplies full history without an explicit previous response ID
- **AND** its stored async prefix contains a settled malformed pair
- **WHEN** the proxy considers preserving the request as a proved owner-bound fresh full resend
- **THEN** that malformed prefix MUST NOT authorize the unanchored fresh-reattach path
- **AND** the existing owner-bound fallback or fail-closed response MUST remain in force
