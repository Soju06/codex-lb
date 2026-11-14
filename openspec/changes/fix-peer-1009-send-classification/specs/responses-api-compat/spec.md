## ADDED Requirements

### Requirement: Peer size rejection during bridge send remains terminal
The HTTP bridge MUST expose a send exception containing received peer close 1009 as HTTP 400 `payload_too_large` of type `invalid_request_error`. It MUST NOT replay or penalize the account and MUST preserve settlement.

#### Scenario: Peer rejects an image send
- **WHEN** an image send observes received peer close 1009
- **THEN** the route returns HTTP 400 payload_too_large with one send and one connection and no account penalty

#### Scenario: Local-only close
- **WHEN** a send fails with local sent 1009 and no received close
- **THEN** existing transport failure handling is preserved
