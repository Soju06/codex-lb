# Change: add subscription overflow fallback to OpenAI-compatible model sources

## Why
codex-lb can already route explicitly selected models to dashboard-managed OpenAI-compatible Model Sources, but subscription-backed Codex traffic stops with `usage_limit_reached` when every eligible ChatGPT account has exhausted upstream quota. Operators need an opt-in overflow path so compatible Responses requests can continue on an external provider without weakening account selection, local admission limits, or continuity safety.

## What changes
- Allow one enabled Responses-capable Model Source to be designated as the subscription overflow fallback, with an optional fallback model override.
- Reuse the Model Source dashboard and encrypted API-key storage; no new per-host fallback credential environment variables are introduced.
- After normal ChatGPT account selection definitively returns aggregate `usage_limit_reached`, retry an account-neutral Responses request against the designated Model Source.
- For a retained `previous_response_id`, fall back only when the request can be projected into a self-contained provider-neutral replay; file-pinned and provider-owned state remains fail-closed.
- Preserve existing behavior for local concurrency/fair-share limits, admission overload, authentication failures, unsupported models, transient upstream errors, and all other non-quota failures.
- Reuse Model Source forwarding, usage settlement, error redaction, request logging, and API-key source-assignment policy for overflow traffic.

## Non-goals
- Falling back on arbitrary HTTP 429 responses or individual-account exhaustion while another ChatGPT account remains eligible.
- Translating Responses requests to providers that only implement Chat Completions.
- Falling back file uploads, compaction, realtime, images, or other account/provider-owned control-plane operations.
- Making the external source a sticky primary after ChatGPT quota becomes available again.
