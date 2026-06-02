## MODIFIED Requirements

### Requirement: API key allowlist allows Cursor aliases

The model allowlist check MUST treat supported Cursor-style GPT-5 aliases as equivalent to their
canonical GPT model when deciding access. A request for the canonical model must be allowed when the key
stores a compatible alias in `allowed_models`.

#### Scenario: Cursor alias allowed model permits canonical request

- **WHEN** a key has `allowed_models: ["gpt-5.4-mini-high"]`
- **AND** a request is made for model `gpt-5.4-mini`
- **THEN** the proxy permits the request because the allowed alias resolves to the requested canonical model

### Requirement: Model catalogs must expose canonical models for alias allowlists

When API-key model allowlists include Cursor-style aliases, the visible model lists MUST expose canonical model IDs and
omit alias-only synthetic IDs so clients see stable model names.

#### Scenario: Model list canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]` and `enforced_model: "gpt-5.4-mini-high"` calls `GET /v1/models`
- **THEN** the response contains the canonical model `gpt-5.4-mini`
- **AND** the response does not expose a synthetic `gpt-5.4-mini-high` model id

#### Scenario: Codex model list visibility canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]`, `enforced_model: "gpt-5.4-mini-high"`, and `apply_to_codex_model=true` calls `GET /backend-api/codex/models`
- **THEN** the canonical `gpt-5.4-mini` entry is visible with `visibility: "list"`
- **AND** other entries are hidden according to the API key allowlist policy
