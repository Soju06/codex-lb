## MODIFIED Requirements

### Requirement: Persisted request logs include provider-aware routing fields
Persisted request logs MUST no longer be account-only records. For provider-aware routing, each persisted request log MUST include provider kind, generic routing-subject identifier, requested route class, and upstream request id when available, even when the request fails before upstream selection.

#### Scenario: Reservation cleanup failure does not override the proxy result
- **WHEN** request handling has already produced a client response
- **AND** best-effort API-key reservation cleanup fails during post-response teardown
- **THEN** the proxy preserves the original response outcome
- **AND** it logs the cleanup failure without replacing the original response with a cleanup error
