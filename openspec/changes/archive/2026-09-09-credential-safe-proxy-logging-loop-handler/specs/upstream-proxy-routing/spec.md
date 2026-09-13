## ADDED Requirements

### Requirement: Dashboard rejects and reports proxy usernames the resolver cannot encode

The dashboard MUST reject an upstream proxy endpoint whose username contains
`:` at creation with a 400 error coded `invalid_proxy_username`, mirroring the
resolver rule (RFC 7617 Basic credentials cannot encode a colon in the
user-id). The endpoint test route MUST report a resolver rejection of an
already persisted endpoint as a failed probe carrying the resolver reason
rather than surfacing an unhandled error.

#### Scenario: Colon username is rejected at creation

- **WHEN** an operator creates an upstream proxy endpoint whose username contains `:`
- **THEN** the request is rejected with a 400 error coded `invalid_proxy_username`

#### Scenario: Endpoint test reports a persisted row the resolver rejects

- **GIVEN** a persisted endpoint the resolver rejects (for example a username containing `:`)
- **WHEN** the endpoint test route is invoked for it
- **THEN** the response reports `ok: false` with the resolver reason as `error` and no status code
- **AND** no probe is sent
