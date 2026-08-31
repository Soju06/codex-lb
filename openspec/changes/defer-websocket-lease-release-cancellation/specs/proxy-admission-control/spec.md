## ADDED Requirements

### Requirement: Direct WebSocket lease release survives cancellation

The proxy MUST clear direct WebSocket ownership before releasing an account connection, stream, or response-create lease. Once ownership is cleared, the proxy MUST finish that release exactly once before propagating cancellation, including repeated cancellation received while release is suspended. Existing non-cancellation release success and failure semantics MUST remain unchanged.

#### Scenario: Cancellation races direct WebSocket connect-attempt release

- **GIVEN** a direct WebSocket connect attempt has cleared its selected stream lease from request ownership
- **WHEN** cancellation is delivered repeatedly while account-lease release is suspended
- **THEN** the release finishes exactly once before cancellation is propagated

#### Scenario: Cancellation races terminal direct WebSocket stream cleanup

- **GIVEN** terminal direct WebSocket cleanup has cleared a request's stream lease from request ownership
- **WHEN** cancellation is delivered while account-lease release is suspended
- **THEN** the release finishes exactly once before cancellation is propagated

#### Scenario: Cancellation races current-account connection cleanup

- **GIVEN** direct WebSocket connection cleanup has cleared the current account lease from connection ownership
- **WHEN** cancellation is delivered repeatedly while account-lease release is suspended
- **THEN** the connection lease release finishes exactly once before cancellation is propagated

#### Scenario: Cancellation races direct WebSocket response-create cleanup

- **GIVEN** direct WebSocket cleanup has cleared a response-create lease from request ownership
- **WHEN** cancellation is delivered while account-lease release is suspended
- **THEN** the response-create lease release finishes exactly once before cancellation is propagated
