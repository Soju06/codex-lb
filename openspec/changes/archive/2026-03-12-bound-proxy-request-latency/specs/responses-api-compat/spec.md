## ADDED Requirements

### Requirement: Streaming Responses requests use a bounded retry budget
When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, and upstream stream attempts. The proxy MUST stop retrying once that budget is exhausted and MUST emit a stable `response.failed` event instead of waiting through repeated full upstream timeouts.

#### Scenario: Request budget expires before another attempt
- **WHEN** a streaming Responses request has consumed its configured request budget before the next retry attempt begins
- **THEN** the proxy emits `response.failed` with a stable timeout code
- **AND** the proxy does not start another upstream attempt

#### Scenario: Stalled stream fails within the shorter idle window
- **WHEN** the upstream opens a Responses stream but does not deliver events before the configured stream idle timeout elapses
- **THEN** the proxy emits `response.failed` for the stalled stream within that idle timeout
- **AND** the same client request does not consume multiple full idle windows retrying the same generic failure

### Requirement: Streaming Responses retries are limited to account-recoverable failures
The proxy MUST automatically retry streaming Responses requests only for failures that are recoverable by refreshing or rotating the selected account. The proxy MUST NOT automatically retry generic upstream failures such as stalled streams, upstream transport failures, or unspecified server errors.

#### Scenario: Account-specific rate limit triggers a retry
- **WHEN** the first upstream streaming event fails with an account-specific rate-limit or quota error that can be resolved by selecting another account
- **THEN** the proxy updates account state for that account
- **AND** the proxy may retry the request on another eligible account while budget remains

#### Scenario: Generic upstream failure does not trigger retry
- **WHEN** the first upstream streaming event fails with `stream_idle_timeout`, `upstream_unavailable`, or another generic upstream error
- **THEN** the proxy forwards that failure to the client
- **AND** the proxy does not automatically retry the same client request
