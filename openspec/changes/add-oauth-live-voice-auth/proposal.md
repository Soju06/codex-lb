## Why

Official Codex uses ChatGPT OAuth for both WebRTC call creation and the sideband WebSocket when the built-in `openai` provider is selected. That provider has no slot for a second Codex-LB Proxy API Key. Codex-LB needs a local keyless caller lane alongside its existing registered-Key lane so both supported client profiles can create and control Live Voice calls.

## What Changes

- Accept registered Proxy API Keys and locally admitted ChatGPT OAuth credentials on the four private Live Voice routes.
- Dispatch `sk-clb-` bearers through existing strict Key validation.
- Admit other bearers only through the existing zero-key proxy origin contract: loopback or an explicitly configured raw-socket CIDR while global API-key authentication is disabled.
- Derive credential-safe caller affinity from a purpose-separated HMAC of the bearer and normalized `chatgpt-account-id`, using the existing persistent encryption key.
- Add one global OAuth Live policy with an explicit upstream account pool.
- Preserve Key assignments, limits, attribution, affinity input, and the documented `requires_openai_auth = true` plus `env_key` Codex profile.
- Add one compact Settings editor: global enable switch, shared upstream pool, and save action.
- Keep immutable exact-owner binding across call creation and sideband.
- Document complete built-in OAuth and registered-Key Codex profiles, including both experimental realtime route overrides.

## Modified Capabilities

- `realtime-api-compat`: dual registered-Key/local-keyless caller admission and exact-owner sideband.
- `database-migrations`: global singleton policy and allowed-account relationship.
- `frontend-architecture`: one Settings-level global policy editor.

## Impact

Realtime caller scope, policy persistence/API, Settings UI, one reversible migration, tests, and user documentation.
