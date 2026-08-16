## MODIFIED Requirements

### Requirement: RequestLog API key reference

The system SHALL record the `api_key_id` in the `request_logs` table for proxy
requests authenticated with an API key. The field MUST be NULL when API key
auth is disabled or the request is unauthenticated. This applies to error rows
as well as successes: when a shared upstream session (e.g. an HTTP-bridge
session multiplexing requests from multiple API keys) fails its pending
requests, each request's log row MUST be attributed to that request's own
authenticated key.

#### Scenario: Authenticated request logged

- **WHEN** a proxy request is authenticated with API key `key-123` and completes
- **THEN** the `request_logs` entry has `api_key_id = "key-123"`

#### Scenario: Unauthenticated request logged

- **WHEN** API key auth is disabled and a proxy request completes
- **THEN** the `request_logs` entry has `api_key_id = NULL`

#### Scenario: Bridge failure fan-out preserves per-request key attribution

- **GIVEN** an HTTP-bridge session holds a pending request authenticated with
  API key `key-123`
- **WHEN** the session fails its pending requests (upstream close, send
  failure, request timeout, or local terminal error)
- **THEN** the request's `request_logs` error entry has
  `api_key_id = "key-123"` even though the session-level failure path has no
  single key of its own
