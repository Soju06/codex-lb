# OAuth WebRTC Live Voice Context

Normative behavior lives in this change's capability delta specs. This file records the client constraint and operator boundary.

## Client constraint

The built-in Codex `openai` provider sends official ChatGPT OAuth as request authorization. The client cannot attach a second Codex-LB Proxy API Key to that provider. The OAuth caller lane lets this first-party profile reach codex-lb while retaining the official provider identity, model behavior, account state, and Live Voice entry point.

The existing registered-Key profile remains a custom provider named `openai`. It combines `requires_openai_auth = true` with `env_key = "CODEX_LB_API_KEY"`: OpenAI authentication keeps the Codex app's ChatGPT capabilities visible, and `env_key` supplies the Codex-LB bearer used by proxy routes. Normal conversations and Live Voice use the same registered Key contract.

## Operator example

1. An operator imports several upstream ChatGPT accounts through existing Codex-LB flows.
2. Under Settings → Live Voice, the operator enables OAuth Live Voice and selects the shared upstream Accounts allowed to serve OAuth calls.
3. Official Codex keeps `model_provider = "openai"` and routes WebRTC call creation to `http://127.0.0.1:2455/backend-api/codex` and sideband to `http://127.0.0.1:2455/v1`.
4. Codex-LB verifies the OAuth principal independently from the imported pool, selects within the configured set, binds the final call owner, and attaches sideband to that owner.
5. Registered Proxy API Key clients keep their existing assignments, limits, logs, affinity digests, and `requires_openai_auth = true` plus `env_key` Codex profile.

## Operational boundary

The policy controls access to pooled upstream accounts and stores no OAuth credentials. Cross-machine authorization-row handling remains outside this feature. `refresh_token_reused` remains an operator-managed recovery event. Logs and acceptance evidence use only routes, status, counts, hashes, and presence booleans.
