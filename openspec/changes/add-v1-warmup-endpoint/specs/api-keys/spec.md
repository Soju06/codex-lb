## MODIFIED Requirements

### Requirement: Weekly token usage tracking
The system SHALL atomically increment `weekly_tokens_used` on the API key record when a non-warmup proxy request completes with token usage data. The token count MUST be `input_tokens + output_tokens`. If token usage is unavailable (error response), the counter MUST NOT be incremented.

#### Scenario: Successful request with usage

- **WHEN** a non-warmup proxy request completes with `input_tokens: 100, output_tokens: 50` for an authenticated key
- **THEN** `weekly_tokens_used` is atomically incremented by 150

#### Scenario: Request with no usage data

- **WHEN** a non-warmup proxy request fails with an error and no usage data is returned
- **THEN** `weekly_tokens_used` is not incremented

#### Scenario: Request without API key auth

- **WHEN** `api_key_auth_enabled` is false and a non-warmup proxy request completes
- **THEN** no API key usage tracking occurs

#### Scenario: Warmup request is excluded from weekly usage tracking

- **WHEN** an authenticated `POST /v1/warmup` execution writes request log rows
- **THEN** those warmup rows are excluded from API key weekly token usage increments
