## ADDED Requirements
### Requirement: Anonymous output ownership
When a started response exists, the proxy SHALL assign response output events without a response ID only to a uniquely identified started response, including a draining response, and SHALL NOT assign them to a request awaiting response.created. When no response has started, existing pre-created output handling SHALL remain supported. Pre-created metadata and error matching SHALL retain their existing semantics.

#### Scenario: Active response with a younger pending request
- **WHEN** response A has started and request B awaits response.created on the same upstream socket
- **THEN** anonymous text, tool, content, and reasoning output events SHALL remain owned by A
- **AND** B SHALL receive no output belonging to A

#### Scenario: Multiple started responses
- **WHEN** more than one response has started and an output event has no response ID
- **THEN** the matcher SHALL leave ownership unresolved rather than assigning the event to a waiting request
