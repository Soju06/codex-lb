## MODIFIED Requirements

### Requirement: Model restriction enforcement

The system SHALL enforce per-key model restrictions in the proxy service layer (not middleware). When `allowed_models` is set (non-null, non-empty) and the requested model is not in the list, the system MUST reject the request. The `/v1/models` endpoint MUST filter the model list based on the authenticated key's `allowed_models`.

For fixed-model endpoints such as `/v1/audio/transcriptions` and `/backend-api/transcribe`, the service MUST evaluate restrictions against fixed effective model `gpt-4o-transcribe`.

`/backend-api/codex/models` SHALL keep the existing allowlist filtering behavior by default. When an authenticated API key has `apply_to_codex_model = true` and `allowed_models` is non-empty, `/backend-api/codex/models` SHALL return the full catalog and rewrite each model entry visibility so allowlisted models use `visibility: "list"` and every other model uses `visibility: "hide"`. When `apply_to_codex_model = true` but `allowed_models` is null or empty, `/backend-api/codex/models` SHALL preserve the original behavior because there is no allowlist to apply.

#### Scenario: Codex models keep filtered behavior by default

- **WHEN** a key has `allowed_models: ["o3-pro"]` and `apply_to_codex_model: false`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response contains only models matching the allowed list

#### Scenario: Codex models rewrite visibility when opted in

- **WHEN** a key has `allowed_models: ["o3-pro"]` and `apply_to_codex_model: true`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response contains the full catalog
- **AND** the `o3-pro` entry has `visibility: "list"`
- **AND** every model not in `allowed_models` has `visibility: "hide"`

#### Scenario: Codex models preserve original behavior without an allowlist

- **WHEN** a key has `allowed_models: null` and `apply_to_codex_model: true`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response preserves the original `/backend-api/codex/models` behavior because there is no allowlist to apply

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again.

The create and edit dialogs SHALL expose an `Apply to codex /model` checkbox directly below `Allowed models`. The checkbox SHALL default to unchecked for new keys and SHALL edit the stored API key value for existing keys.

#### Scenario: Create key with codex model visibility option

- **WHEN** an admin opens the create API key dialog
- **THEN** the `Apply to codex /model` checkbox appears directly below `Allowed models`
- **AND** it is unchecked by default

#### Scenario: Edit key with stored codex model visibility option

- **WHEN** an admin opens the edit API key dialog for a key with `apply_to_codex_model: true`
- **THEN** the `Apply to codex /model` checkbox is shown as checked
