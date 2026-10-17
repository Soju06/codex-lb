## MODIFIED Requirements

### Requirement: Warmup endpoint is exposed on the v1 proxy surface
The system SHALL expose `POST /v1/warmup` on the same authenticated proxy surface as other `/v1/*` routes. The endpoint SHALL accept a JSON body with `mode` and SHALL return HTTP 200 with a structured JSON summary of submitted, skipped, and failed account warmups for every valid execution. Per-account `ProxyAuthError` and `ProxyRateLimitError` failures SHALL be represented in the `failed` summary regardless of the number of target accounts.

The system SHALL also expose `POST /v1/warmup/{mode}` on the same authenticated proxy surface. That route SHALL not require a request body and SHALL execute the same warmup behavior as the body-based route for the supplied `mode`.

#### Scenario: Authenticated warmup request succeeds
- **WHEN** a client calls `POST /v1/warmup` with a valid API key and valid mode
- **THEN** the system returns 200 with a per-account warmup result summary

#### Scenario: Single-account authentication failure returns summary
- **WHEN** a valid warmup request targets exactly one account and its submission raises `ProxyAuthError`
- **THEN** the system returns 200 with `total_accounts=1` and one `failed` entry with error code `auth_error`

#### Scenario: Single-account rate-limit failure returns summary
- **WHEN** a valid warmup request targets exactly one account and its submission raises `ProxyRateLimitError`
- **THEN** the system returns 200 with `total_accounts=1` and one `failed` entry with error code `rate_limit_exceeded`

#### Scenario: Invalid mode is rejected
- **WHEN** a client calls `POST /v1/warmup` with an unsupported mode value
- **THEN** the system returns a 400 invalid request error

#### Scenario: Path-based warmup request succeeds without a body
- **WHEN** a client calls `POST /v1/warmup/normal` with a valid API key and no request body
- **THEN** the system returns 200 with the same per-account warmup result summary as the body-based route
