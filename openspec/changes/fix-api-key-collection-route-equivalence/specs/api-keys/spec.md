## ADDED Requirements

### Requirement: API-key collection routes preserve trailing-slash behavior

The API-key collection operations MUST serve both `/api/api-keys` and
`/api/api-keys/` directly. Equivalent route forms MUST use the same
authentication, validation, persistence, and response contracts, and the
unslashed form MUST NOT depend on an HTTP redirect or the dashboard SPA
fallback.

#### Scenario: List API keys through either collection URL

- **WHEN** a dashboard client sends `GET /api/api-keys` or
  `GET /api/api-keys/`
- **THEN** both requests return the same API-key collection response directly
- **AND** neither request returns an HTTP redirect

#### Scenario: Create an API key through either collection URL

- **WHEN** a dashboard client sends the same valid creation payload to
  `POST /api/api-keys` or `POST /api/api-keys/`
- **THEN** both requests run the API-key creation operation directly
- **AND** neither request depends on redirect handling to preserve the request
  body
