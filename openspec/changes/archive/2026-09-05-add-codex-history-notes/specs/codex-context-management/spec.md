## ADDED Requirements

### Requirement: Explicit context backend endpoints
The proxy SHALL accept POST under `/backend-api/codex/alpha/history/v2/` for `list_windows`, `list_items`, `read_item`, and `search_contents`, and under `/backend-api/codex/alpha/notes/v2/` for `thread_hint`, `list_files_by_prefix`, `read_file`, `search_contents`, `append_to_file`, and `write_file`. It MUST NOT provide a wildcard upstream path relay. The same endpoints with one trailing slash SHALL accept the same POST method and body.

#### Scenario: Supported operation reaches the backend
- **WHEN** an authorized client sends a supported operation with opaque JSON bytes
- **THEN** the proxy forwards the unchanged method, body, query parameters and encryption/truncation headers to the corresponding upstream Codex path
- **AND** it returns the upstream success status, body and allowed response headers

### Requirement: Explicit single-account scope
Context endpoints MUST require a valid proxy API key even when optional global proxy authentication is disabled. The key MUST have account-assignment scope enabled with exactly one account. Unscoped or multi-account keys MUST receive HTTP 409 with code `context_account_scope_required` before any upstream request. Account selection and token refresh MUST remain within the assigned account and MUST NOT rotate a context operation to another account on failure. The same scoped key MUST be used for the corresponding Responses traffic. Changes to the key's assigned account are outside the continuity guarantee and require a new Codex session.

#### Scenario: Multiple eligible accounts are rejected
- **GIVEN** a valid key with zero or multiple assigned accounts
- **WHEN** a context endpoint is called
- **THEN** the proxy rejects it without contacting any upstream account

#### Scenario: Assigned owner is unavailable
- **GIVEN** a context key assigned to account A and another healthy account B
- **WHEN** A is unavailable or an upstream operation fails
- **THEN** the proxy does not forward the operation to B

### Requirement: Private context transport
The proxy MUST treat request bodies as opaque and MUST NOT include context contents, private upstream error text, or credential-bearing header values in control-request diagnostics or upstream payload tracing. Successful response bodies MUST remain unchanged. Final upstream failures MUST retain their HTTP error status and use a generic OpenAI error envelope with code `context_backend_unavailable`. Unexpected transport failures MUST return a generic HTTP 503 response.

#### Scenario: Upstream echoes sensitive content in an error
- **WHEN** an upstream failure contains private note text
- **THEN** the downstream error and proxy diagnostics do not contain that text

#### Scenario: Encryption metadata passes unchanged
- **WHEN** Codex includes `x-openai-encrypted-tool-arguments` and `x-openai-tool-output-truncation-policy`
- **THEN** the proxy forwards both headers without decoding, reserializing or modifying the body
