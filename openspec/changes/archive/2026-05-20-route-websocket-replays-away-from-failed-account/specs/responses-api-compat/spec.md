## ADDED Requirements

### Requirement: Fresh websocket replays avoid failed accounts
When an upstream websocket closes or rejects send while a pre-created Responses request can be replayed as a fresh request, the service MUST record a transient upstream stream error for the failed account and MUST exclude that account from account selection for the replay reconnect.

#### Scenario: replay reconnect skips account that dropped the socket
- **WHEN** a direct Responses websocket request is pending without previous_response_id
- **AND** the upstream websocket closes before the request receives a terminal response event
- **AND** the request is eligible for transparent replay
- **THEN** the service records a transient stream error for the account that dropped the socket
- **AND** the replay reconnect excludes that account from selection

#### Scenario: previous-response replay keeps owner affinity
- **WHEN** a direct Responses websocket follow-up request includes previous_response_id
- **AND** the upstream websocket closes before the request receives a terminal response event
- **AND** the request is eligible for transparent replay
- **THEN** the service records a transient stream error for the account that dropped the socket
- **AND** the replay reconnect does not exclude the previous-response owner account solely because it dropped the socket
