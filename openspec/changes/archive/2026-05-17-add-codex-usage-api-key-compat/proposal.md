# add-codex-usage-api-key-compat

## Why
OpenCodeBar calls `/api/codex/usage` with the same LB API key it uses for `/v1/*` proxy calls. In 1.13.x that route only accepts ChatGPT caller identity (`chatgpt-account-id` + ChatGPT token), so API-key clients fail with `Missing chatgpt-account-id header` even though codex-lb already has self-service usage data for API keys.

## What Changes
- Allow `/api/codex/usage` to authenticate LB API-key callers in addition to ChatGPT callers.
- Keep existing ChatGPT identity validation unchanged when `chatgpt-account-id` is present.
- Return a `RateLimitStatusPayload` compatibility response for API-key callers using the existing self-usage and credit-limit data.

## Impact
- OpenCodeBar and similar API-key-based codex clients can call `/api/codex/usage` without sending `chatgpt-account-id`.
- Existing browser/ChatGPT-style callers continue to use the current identity validation path.
