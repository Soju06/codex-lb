# Keyless OAuth WebRTC Live Voice Context

Normative behavior lives in this change's capability delta specs. This file records the client constraint and operator boundary.

## Client constraint

The built-in Codex `openai` provider sends official ChatGPT OAuth as request authorization. The client cannot attach a second Codex-LB Proxy API Key to that provider. The keyless OAuth caller lane lets this first-party profile reach codex-lb from the same local/trusted origin boundary already used by ordinary zero-key proxy requests.

The registered-Key profile combines `requires_openai_auth = true` with `env_key = "CODEX_LB_API_KEY"`: OpenAI authentication keeps the Codex app's ChatGPT capabilities visible, and `env_key` supplies the Codex-LB bearer used by proxy routes.

## Operator example

1. An operator imports several upstream ChatGPT accounts through existing Codex-LB flows.
2. Under Settings → Live Voice, the operator enables OAuth Live Voice and selects the shared upstream Accounts allowed to serve keyless calls.
3. Official Codex keeps `model_provider = "openai"` and routes WebRTC call creation to `http://127.0.0.1:2455/backend-api/codex` and sideband to `http://127.0.0.1:2455/v1`.
4. Codex-LB applies the existing zero-key origin check, derives a private credential-pair affinity digest, selects within the configured pool, binds the final call owner, and attaches sideband to that owner.
5. Registered Proxy API Key clients keep their existing assignments, limits, logs, affinity digests, and Key profile.

## Operational boundary

Loopback needs no configuration. `proxy_unauthenticated_client_cidrs` remains an existing advanced opt-in for explicitly trusted raw socket peers. Other remote callers use registered Proxy API Keys.

The policy stores no OAuth credentials. Logs and acceptance evidence use only routes, status, counts, hashes, and presence booleans.
