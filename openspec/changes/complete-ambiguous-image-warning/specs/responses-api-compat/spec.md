## ADDED Requirements

### Requirement: Ambiguous image transport failures disclose execution risk
An admitted image request sent without acknowledgement SHALL retain its existing HTTP status and error code on generic disconnect, clean close 1000 or uncertain send exception. Its error message SHALL warn that the request may still be executing and retrying may duplicate upstream work. Automatic retry decisions and counts SHALL remain unchanged. Text-only failures and payload_too_large (including peer 1009) SHALL NOT receive this warning.

#### Scenario: Unacknowledged image transport failure
- **WHEN** an admitted image send fails ambiguously or disconnects before acknowledgement
- **THEN** the HTTP error warns of ongoing execution and duplicate work after one send without replay

#### Scenario: Explicit payload rejection
- **WHEN** upstream reports peer 1009
- **THEN** payload_too_large retains its existing terminal HTTP 400 behavior without the ambiguous warning

#### Scenario: Text control
- **WHEN** a text-only request encounters a disconnect or send exception
- **THEN** its existing retry and error contract remains unchanged
