## ADDED Requirements

### Requirement: Native helper compatibility is negotiated before dispatch

Each newly started native helper generation MUST complete a bounded,
versioned client/server hello exchange before accepting an HTTP or WebSocket
command. The Python adapter MUST require the negotiated protocol version and
every capability used by its current call sites. Incompatibility MUST fail
before dispatch and MUST NOT become a missing-helper Python fallback.

#### Scenario: Installed helper is incompatible

- **WHEN** negotiation times out, is malformed, selects an unsupported version, or lacks a required capability
- **THEN** the adapter terminates the process before dispatch
- **AND** the operation fails without Python replay
