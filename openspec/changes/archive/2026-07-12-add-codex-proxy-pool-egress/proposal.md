# Change: add Codex proxy-pool upstream egress

## Why
All account-scoped ChatGPT/OpenAI/Codex upstream traffic must obey the proxy configured for that account. A partial per-request proxy setting is unsafe because other upstream surfaces could still go through the default pool, environment proxy, or direct egress.

## What changes
- Add strict account-bound/default proxy-pool route resolution for ChatGPT upstream egress.
- Replace affected upstream aiohttp/websockets calls with the Codex upstream client using one built-in Codex CLI TLS fingerprint.
- Fail closed before network open when an account-bound route is unavailable.
- Record route mode, pool, endpoint, fallback, and fail-closed reason in request logs.

## Non-goals
- No configurable TLS profiles or environment-selected fingerprint.
- No generic transport adapter abstraction.
- No direct/default fallback for explicitly account-bound traffic.
