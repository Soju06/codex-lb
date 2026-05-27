## ADDED Requirements

### Requirement: Upstream overload envelopes are classified as retryable transient failures

When `classify_upstream_failure` observes an upstream error envelope whose `code` is `overloaded_error`, the system MUST treat it as `retryable_transient` regardless of the accompanying HTTP status. Streamed Responses API traffic can deliver the overload envelope on a connection that has already returned HTTP 200, so a 5xx-only heuristic is insufficient to drive account fail-over and bounded retry.

#### Scenario: `overloaded_error` without a 5xx status is retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` not in the 5xx range (including `None`)
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the failover layer is eligible to retry the request or fail over to another account instead of returning a non-retryable error to the client

#### Scenario: `overloaded_error` with a 5xx status remains retryable transient

- **WHEN** `classify_upstream_failure` is called with `error_code="overloaded_error"` and `http_status` is 500, 502, 503, or 504
- **THEN** the returned `failure_class` is `retryable_transient`
- **AND** the result is the same as the no-status path, so the 5xx fallback heuristic is not the only signal driving the decision
