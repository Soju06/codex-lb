## ADDED Requirements

### Requirement: API key enforcement on Anthropic Messages route
When API key authentication is enabled, `POST /v1/messages` MUST enforce the
same API key validation and request-model limit reservation flow as other proxy
routes.

#### Scenario: Missing or invalid API key on /v1/messages
- **WHEN** API key auth is enabled and a client calls `/v1/messages` without a
  valid Bearer token
- **THEN** the service rejects the request with an authentication error

#### Scenario: Allowed model restriction on /v1/messages
- **WHEN** an API key has `allowed_models` and request `model` is not allowed
- **THEN** the service rejects the request before upstream forwarding

#### Scenario: Reservation settlement on /v1/messages success
- **WHEN** a request to `/v1/messages` completes with usage information
- **THEN** the service finalizes the API key usage reservation using the
  resolved model and token counts

#### Scenario: Reservation settlement on /v1/messages failure
- **WHEN** a request to `/v1/messages` fails or lacks usage information
- **THEN** the service releases the API key usage reservation
