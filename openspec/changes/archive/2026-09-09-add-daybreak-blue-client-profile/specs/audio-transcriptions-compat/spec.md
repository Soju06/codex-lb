## ADDED Requirements

### Requirement: Daybreak capability intent fails closed before transcription parsing

`POST /backend-api/transcribe` and `POST /v1/audio/transcriptions` MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present, even when deployment-wide API-key authentication is disabled. After authentication they MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before multipart parsing, model-source lookup, usage reservation, account selection, or upstream dispatch. Headerless transcription requests MUST retain their existing behavior.

#### Scenario: Authenticated carrier is denied before transcription parsing

- **WHEN** a valid proxy API key sends either transcription route with the Daybreak carrier
- **THEN** the route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no multipart body is parsed and no model source, reservation, account, or upstream request is selected

#### Scenario: Headerless transcription behavior remains unchanged

- **WHEN** a transcription request omits the required-capability carrier
- **THEN** the existing authentication, parsing, policy, account-routing, and response behavior remains in effect
