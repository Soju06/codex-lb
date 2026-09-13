## ADDED Requirements

### Requirement: Required-capability header authenticates through the existing proxy API-key dependency

Whenever a protected proxy request carries one or more `X-Codex-LB-Required-Capability` values, the existing `validate_proxy_api_key` Security dependency MUST require a valid proxy API key before the handler runs, even when `api_key_auth_enabled` is false and the caller would otherwise qualify as local or CIDR-allowlisted. Headerless requests MUST retain the existing global-switch behavior. The capability header MUST NOT introduce a second FastAPI authentication dependency identity for ordinary proxy routes.

#### Scenario: Capability header requires a key while global auth is disabled

- **WHEN** `api_key_auth_enabled` is false
- **AND** a local or CIDR-allowlisted client sends a protected proxy request with `X-Codex-LB-Required-Capability`
- **THEN** ingress requires a valid proxy API key
- **AND** a missing or invalid key is rejected with the existing `401 invalid_api_key` error

#### Scenario: Headerless requests keep the global authentication switch

- **WHEN** `api_key_auth_enabled` is false
- **AND** a local or CIDR-allowlisted client sends a protected proxy request without `X-Codex-LB-Required-Capability`
- **THEN** the request proceeds without a new per-request API-key requirement
