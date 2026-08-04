## ADDED Requirements

### Requirement: Routed WebSocket connection failures preserve transport errors

The Codex upstream client MUST invoke an asynchronous WebSocket context
manager's exit method only after that context manager has been entered
successfully. If context entry fails, the client MUST preserve the original
connection or handshake failure for credential-safe transport classification
and MUST apply the configured same-pool endpoint fallback policy to that
failure.

#### Scenario: Connection failure before context entry is not masked

- **GIVEN** an awaitable WebSocket context manager whose connection attempt fails before entry completes
- **WHEN** the Codex upstream client opens the routed WebSocket
- **THEN** the client does not invoke the unentered context manager's exit method
- **AND** it returns a credential-safe transport error classified from the original connection failure
- **AND** it does not replace that failure with a cleanup exception

#### Scenario: Unmasked failure can use the next route endpoint

- **GIVEN** a routed WebSocket connection whose first endpoint fails before context entry
- **AND** same-pool network-error fallback is enabled
- **WHEN** another endpoint remains in the resolved route
- **THEN** the client attempts the next endpoint
- **AND** the first endpoint's unentered context manager is not exited

#### Scenario: Successfully entered context retains caller-owned cleanup

- **GIVEN** a routed WebSocket context manager enters successfully
- **WHEN** the client returns the opened WebSocket and its context to the caller
- **THEN** the caller can exit the returned context using the existing ownership contract
