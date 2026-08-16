## ADDED Requirements

### Requirement: HTTP SSE stream idle timeouts remain account-neutral

When an HTTP SSE Responses stream's first upstream event is `response.failed` with `code=stream_idle_timeout`, the proxy MUST exclude that account from the remainder of the same request and MAY fail over to another account. It MUST NOT write account error-health (`record_error`, rate-limit, quota, or permanent failure) for that idle timeout. Request logs MUST still record `stream_idle_timeout` on the idle attempt.

#### Scenario: First-event stream idle timeout failovers without health penalty

- **GIVEN** an HTTP SSE Responses stream whose first upstream event is `response.failed` with `code=stream_idle_timeout`
- **AND** another healthy account is available
- **WHEN** the proxy retries the request
- **THEN** the idle account is excluded from the remainder of this request
- **AND** the idle account receives no error-health write
- **AND** the client receives the later account's successful stream
- **AND** the idle attempt's request log still uses `error_code=stream_idle_timeout`
