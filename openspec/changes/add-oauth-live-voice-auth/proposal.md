## Why

Official Codex uses ChatGPT OAuth for both WebRTC call creation and the sideband WebSocket when the built-in `openai` provider is selected. That provider has no slot for a second Codex-LB Proxy API Key. Codex-LB needs an OAuth caller lane alongside its existing registered-Key lane so both supported client profiles can create and control Live Voice calls.

## What Changes

- Accept registered Proxy API Keys and verified ChatGPT OAuth principals on the four private Live Voice routes.
- Dispatch `sk-clb-` bearers through existing strict Key validation and other bearers through verified ChatGPT OAuth identity resolution.
- Validate OAuth credentials upstream, derive a stable principal independently from imported serving accounts, and retain bounded cache/singleflight behavior.
- Add one global OAuth Live policy with an explicit upstream account pool.
- Preserve Key assignments, limits, attribution, affinity input, and the documented `requires_openai_auth = true` plus `env_key` Codex profile.
- Add one compact Settings editor: global enable switch, shared upstream pool, and save action.
- Keep immutable exact-owner binding across call creation and sideband.
- Document complete built-in OAuth and registered-Key Codex profiles, including both experimental realtime route overrides.

## Modified Capabilities

- `realtime-api-compat`: dual Key/OAuth caller authentication and exact-owner sideband.
- `account-identity`: verified OAuth principals can exist independently from imported accounts.
- `database-migrations`: global singleton policy and allowed-account relationship.
- `frontend-architecture`: one Settings-level global policy editor.

## Impact

- OAuth identity resolution, realtime caller scope, policy persistence/API, Settings UI, one reversible migration, tests, and user documentation.
