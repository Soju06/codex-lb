## ADDED Requirements

### Requirement: Merge OpenAI-compatible images API

The upstream OpenAI-compatible images API MUST be adopted with its request translation, schema validation, and response mapping behavior intact. The merged implementation MUST route image generation through the existing proxy admission and account-selection controls instead of creating a bypass path.

#### Scenario: Images request uses proxy controls

- **WHEN** a client sends a valid OpenAI-compatible image generation request
- **THEN** the service validates and translates the request according to the images API contract
- **AND** account selection, request admission, auth, rate-limit accounting, and error handling run through the same merged proxy controls used by other public proxy routes

#### Scenario: Invalid images request is rejected consistently

- **WHEN** a client sends an invalid images API payload
- **THEN** the service returns a stable OpenAI-style error envelope
- **AND** the request is not forwarded upstream
