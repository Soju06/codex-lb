## ADDED Requirements

### Requirement: Merge upstream file upload protocol without losing account affinity

The upstream file upload protocol MUST be adopted so backend file creation and finalization are available through the merged proxy surface. File identifiers created through the protocol MUST preserve the upstream account affinity needed by later Responses requests that reference those files.

#### Scenario: Uploaded file drives later Responses routing

- **GIVEN** a client uploads a file through the merged backend files protocol
- **WHEN** a later Responses request references the uploaded `file_id`
- **THEN** routing uses the account affinity recorded for that file
- **AND** the request is not routed to an unrelated upstream account

#### Scenario: File protocol merge keeps existing proxy protections

- **GIVEN** the merged tree contains upstream files protocol code
- **WHEN** a file operation fails due to timeout, upstream error, or invalid payload
- **THEN** the service returns an OpenAI-style error envelope or existing proxy error shape appropriate to that route
- **AND** existing request admission, auth, and rate-limit protections remain in force
