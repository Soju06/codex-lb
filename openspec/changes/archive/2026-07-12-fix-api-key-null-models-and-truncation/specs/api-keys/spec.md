## MODIFIED Requirements

### Requirement: Model restriction enforcement

The system SHALL enforce per-key model restrictions in the proxy service layer (not middleware). When `allowed_models` is set (non-null, non-empty) and the requested model is not in the list, the system MUST reject the request. When reading stored `allowed_models`, JSON `null`, blank strings, and non-string array entries MUST be ignored and MUST NOT become model names. The `/v1/models` endpoint MUST filter the model list based on the authenticated key's `allowed_models`.

#### Scenario: Stored null allowed-model entries are ignored

- **GIVEN** an API key row stores `allowed_models` as `[null, "gpt-5.2", 42, ""]`
- **WHEN** the key policy is loaded
- **THEN** the effective allowed model list is `["gpt-5.2"]`
- **AND** `null` is not converted to `"None"`
