## MODIFIED Requirements

### Requirement: Optional upstream request summary tracing

When `log_upstream_request_summary` is enabled, the system MUST log one start record and one completion record for each outbound upstream proxy request. For provider-aware routing, each record MUST include the proxy `request_id`, requested route class, selected provider kind, selected routing-subject identifier when available, and enough metadata to correlate the request with the result.

#### Scenario: Provider-aware upstream request tracing is enabled

- **WHEN** the proxy sends an upstream request while `log_upstream_request_summary=true`
- **THEN** the console shows start and completion records that include provider kind, route class, routing-subject identifier or label, and upstream request id when the provider returns one

### Requirement: Persisted request logs include provider-aware routing fields

Persisted request logs MUST no longer be account-only records. For provider-aware routing, each persisted request log MUST include provider kind, generic routing-subject identifier, requested route class, and upstream request id when available, even when the request fails before upstream selection.

#### Scenario: Persisted request log records a selected provider

- **WHEN** a proxied request selects an upstream routing subject
- **THEN** the persisted request log includes provider kind, routing-subject identifier, route class, and upstream request id when present

#### Scenario: Persisted request log records a pre-routing capability rejection

- **WHEN** the proxy rejects a request before upstream selection because no provider supports the requested route, transport, or continuity capability
- **THEN** the persisted request log still records the requested route class and normalized rejection reason without requiring an `account_id`

### Requirement: Proxy 4xx/5xx responses are logged with provider-aware rejection detail

When the proxy returns a 4xx or 5xx response for a proxied request, the system MUST log the request id, method, path, status code, error code, and error message to the console. When the failure is caused by provider capability gating before routing-subject selection, the log MUST also include the requested route class and rejection reason.

#### Scenario: Provider capability mismatch is rejected before selection

- **WHEN** the proxy rejects a request before upstream selection because no provider supports the requested route, transport, or continuity capability
- **THEN** the console log includes the requested route class and normalized rejection code

### Requirement: Provider health transitions are logged with provider context

When provider health changes because of validation or repeated upstream auth failures, the system MUST log the provider kind, routing-subject identifier, and normalized failure reason.

#### Scenario: Platform auth failure changes provider health

- **WHEN** an `openai_platform` identity transitions to unhealthy or deactivated after repeated auth failures
- **THEN** the runtime log includes provider kind, routing-subject identifier, and the normalized provider-auth failure reason
