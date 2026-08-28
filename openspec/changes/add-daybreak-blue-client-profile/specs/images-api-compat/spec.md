## ADDED Requirements

### Requirement: Daybreak capability intent fails closed on Images HTTP routes

The Codex-base and `/v1` image generation and edit routes MUST require a valid proxy API key whenever `X-Codex-LB-Required-Capability` is present, even when deployment-wide API-key authentication is disabled. After authentication they MUST return HTTP 400 with `error.code = "required_capability_transport_unsupported"` before request-body parsing that is not already required by framework validation, model-source lookup, usage reservation, account selection, internal Responses construction, or upstream dispatch. The rejection MUST emit exactly one bounded `images_route_complete` observation. Headerless Images requests MUST preserve their existing behavior.

#### Scenario: Authenticated Daybreak image request fails closed

- **WHEN** the Daybreak provider sends a generation or edit request with its valid proxy API key and capability carrier
- **THEN** the Images route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no model source, reservation, account, internal Responses request, or upstream attempt is selected
- **AND** exactly one bounded invalid-request route observation is emitted

#### Scenario: Daybreak image request authenticates before transport denial

- **WHEN** a capability-bearing generation or edit request omits the proxy API key or supplies an invalid key
- **THEN** the Images route returns the existing HTTP 401 `invalid_api_key` response
- **AND** no image body is decoded and no account or upstream request is selected

#### Scenario: Ordinary Images behavior remains unchanged

- **WHEN** an Images request omits the required-capability carrier
- **THEN** the route retains its existing authentication, validation, account-routing, observability, and response behavior
