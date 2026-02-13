## 1. Auth Boundary

- [x] 1.1 Update middleware so `/api/codex/usage` always requires codex bearer caller validation and is not unlocked by dashboard session auth
- [x] 1.2 Keep `/v1/*` and `/backend-api/codex/*` under API key middleware scope

## 2. Codex Bearer Validation

- [x] 2.1 Add repository lookup for active LB membership by `chatgpt_account_id`
- [x] 2.2 Validate bearer token/account pair via upstream usage call before allowing `/api/codex/usage`

## 3. Tests

- [x] 3.1 Add integration test: password mode + dashboard session only (no codex caller identity) => 401
- [x] 3.2 Add integration test: password mode + logged out + valid bearer/account-id + registered `chatgpt_account_id` => 200
- [x] 3.3 Add integration test: password mode + logged out + unknown `chatgpt_account_id` => 401
- [x] 3.4 Keep existing `/api/codex/usage` aggregate behavior assertions intact
