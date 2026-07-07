## Context

Codex CLI 0.142.x reads reset-credit availability from the top-level
`rate_limit_reset_credits` object on `/api/codex/usage` and redeems a credit by
posting `redeem_request_id` to `/api/codex/rate-limit-reset-credits/consume`.
codex-lb already validates ChatGPT usage callers by calling upstream usage with
the caller bearer token and registered `chatgpt-account-id`, but it discarded the
reset-credit summary and had no compatible consume route.

## Goals / Non-Goals

**Goals:**

- Preserve upstream reset-credit availability for ChatGPT-authenticated Codex usage callers.
- Forward reset redemption through the same upstream route policy used for usage validation.
- Let dashboard operators consume one selected account reset credit after confirmation.
- Refresh codex-lb's local usage snapshot after upstream reports a successful or idempotently successful reset.

**Non-Goals:**

- Do not create local reset-credit accounting.
- Do not let codex-lb API-key callers consume ChatGPT account reset credits.
- Do not change aggregate usage-window semantics.

## Decisions

- Upstream ChatGPT remains the source of truth for reset-credit availability and consume outcome. codex-lb only preserves the availability summary and returns the upstream consume response.
- The consume endpoint reuses `validate_codex_usage_identity` so the bearer token, `chatgpt-account-id`, local account id, upstream route, and upstream usage payload are resolved once at the request boundary.
- The upstream client posts `redeem_request_id` to `/wham/rate-limit-reset-credits/consume`, matching the existing ChatGPT backend usage path rather than introducing separate local state.
- Dashboard reset actions generate their own `redeem_request_id` server-side and reuse stored account tokens; they do not require exposing ChatGPT bearer tokens to the browser.
- `reset` and `already_redeemed` both trigger a forced local usage refresh because both outcomes mean the redemption request has settled upstream.

## Risks / Trade-offs

- Upstream contract drift could change consume response codes -> typed validation raises an upstream error instead of inventing fallback behavior.
- A successful upstream reset followed by local refresh failure could leave codex-lb briefly stale -> the next scheduled usage refresh still reconciles from upstream.
- Revalidating usage before consume adds one upstream call -> it keeps caller identity and route ownership consistent with existing Codex usage auth.
