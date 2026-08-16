## ADDED Requirements

### Requirement: Daybreak capability intent fails closed on Chat Completions

`POST /v1/chat/completions` MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present, even when deployment-wide API-key authentication is disabled. After authentication it MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before model-source lookup, usage reservation, account selection, Responses conversion, or upstream dispatch. Headerless Chat Completions requests MUST retain their existing behavior.

#### Scenario: Authenticated carrier is denied before chat routing

- **WHEN** a valid proxy API key sends a Chat Completions request with the Daybreak capability carrier
- **THEN** the route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no model source, reservation, account, Responses request, or upstream attempt is selected

#### Scenario: Headerless chat behavior remains unchanged

- **WHEN** a Chat Completions request omits the required-capability carrier
- **THEN** the route retains its existing authentication, validation, routing, and response behavior
