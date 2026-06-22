## Why

`POST /v1/reset-credit` currently decrypts and forwards the persisted access token for the selected account without first refreshing it. When that stored token is expired or past the normal refresh threshold, self-service reset-credit redemption fails upstream with 401 until some unrelated traffic refreshes the account.

## What Changes

- Refresh the target account via `AuthManager.ensure_fresh` before decrypting the bearer token used by `POST /v1/reset-credit`.
- Keep the self-service redemption path aligned with the dashboard reset-credit consume flow so both surfaces redeem against fresh credentials.

## Impact

- API-key-authenticated self-service reset-credit redemption succeeds for accounts whose stored token is stale but refreshable.
- No request or response schema changes.
