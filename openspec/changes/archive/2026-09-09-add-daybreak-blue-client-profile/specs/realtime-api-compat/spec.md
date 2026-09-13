## ADDED Requirements

### Requirement: Daybreak capability intent fails closed on private Realtime transports

Capability-bearing private Realtime requests MUST authenticate and fail closed on unsupported transports. `POST /backend-api/codex/realtime/calls` and non-Responses WebSocket handshakes at `/backend-api/codex/{call_id}`, `/v1/live/{call_id}`, and `/v1/realtime` MUST validate the registered proxy API key before returning HTTP 400 with `error.code = "required_capability_transport_unsupported"`. Call creation MUST deny before account selection or owner binding. WebSocket handshakes MUST deny before acceptance, call-owner lookup, lease acquisition, or upstream connection. Headerless Realtime requests MUST retain their existing required-key and exact-owner behavior.

#### Scenario: Capability-bearing call creation is denied before selection

- **WHEN** a valid registered key sends private Realtime call creation with the Daybreak carrier
- **THEN** the route returns HTTP 400 `required_capability_transport_unsupported`
- **AND** no account is selected, no upstream call is created, and no owner is bound

#### Scenario: Capability-bearing Live WebSocket is denied before owner lookup

- **WHEN** a valid registered key opens any supported non-Responses Realtime WebSocket with the Daybreak carrier
- **THEN** the handshake receives HTTP 400 `required_capability_transport_unsupported`
- **AND** the route does not accept the WebSocket, resolve a call owner, acquire a lease, or connect upstream

#### Scenario: Realtime carrier authenticates before transport denial

- **WHEN** a capability-bearing call-creation request or Live WebSocket omits its key or supplies an invalid key
- **THEN** ingress returns the existing HTTP 401 `invalid_api_key` response
- **AND** no account or owner resolution occurs

#### Scenario: Headerless Realtime behavior remains unchanged

- **WHEN** a Realtime call or sideband request omits the required-capability carrier
- **THEN** the existing registered-key, immutable-owner, and transport behavior remains in effect
