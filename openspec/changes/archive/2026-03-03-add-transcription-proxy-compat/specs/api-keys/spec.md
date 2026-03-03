## MODIFIED Requirements

### Requirement: API Key Bearer authentication guard

The system SHALL validate API keys on proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`.

`/api/codex/usage` SHALL NOT be covered by the API key auth guard scope.

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: API key guard route scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/v1/responses`, `/backend-api/codex/responses`, `/v1/audio/transcriptions`, or `/backend-api/transcribe`
- **THEN** the API key guard validation is applied

#### Scenario: Codex usage excluded from API key guard scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key guard validation is not applied

#### Scenario: Valid API key injected into handler

- **WHEN** `api_key_auth_enabled` is true and a valid Bearer token is provided
- **THEN** the route handler receives a typed `ApiKeyData` parameter (not `request.state`)

#### Scenario: API key auth disabled returns None

- **WHEN** `api_key_auth_enabled` is false
- **THEN** the dependency returns `None` and the request proceeds without authentication

### Requirement: Model restriction enforcement

The system SHALL enforce per-key model restrictions in the proxy service layer (not middleware). When `allowed_models` is set (non-null, non-empty) and the requested model is not in the list, the system MUST reject the request. The `/v1/models` endpoint MUST filter the model list based on the authenticated key's `allowed_models`.

For fixed-model endpoints such as `/v1/audio/transcriptions` and `/backend-api/transcribe`, the service MUST evaluate restrictions against fixed effective model `gpt-4o-transcribe`.

#### Scenario: Requested model not allowed

- **WHEN** a key has `allowed_models: ["o3-pro"]` and a request is made for model `gpt-4.1`
- **THEN** the proxy returns 403 with OpenAI-format error `{ "error": { "code": "model_not_allowed", "message": "This API key does not have access to model 'gpt-4.1'" } }`

#### Scenario: All models allowed

- **WHEN** a key has `allowed_models: null`
- **THEN** any model is permitted

#### Scenario: Model list filtered

- **WHEN** a key with `allowed_models: ["o3-pro"]` calls `GET /v1/models`
- **THEN** the response contains only models matching the allowed list

#### Scenario: No API key auth (disabled)

- **WHEN** `api_key_auth_enabled` is false and a request is made to `/v1/models`
- **THEN** the full model catalog is returned

#### Scenario: Fixed transcription model not allowed

- **WHEN** a key has `allowed_models: ["gpt-5.1"]` and a request is made to `/v1/audio/transcriptions` or `/backend-api/transcribe`
- **THEN** the proxy returns 403 with OpenAI-format error code `model_not_allowed` for model `gpt-4o-transcribe`
