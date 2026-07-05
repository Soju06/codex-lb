## Why

Operators need to expose self-hosted or third-party OpenAI-compatible model
endpoints, such as vLLM, through the same downstream API-key and usage-accounting
surface used for subscription-backed Codex traffic. Today the model catalog is
effectively tied to ChatGPT subscription accounts, and forcing vLLM endpoints
into the `accounts` abstraction would incorrectly inherit OAuth refresh,
ChatGPT account headers, plan/quota routing, websocket bridge, and
continuity-owner invariants that do not apply to OpenAI-compatible endpoints.

This also aligns with GitHub issue #1082, which asks for first-class external
model routing and Codex model-picker entries for providers such as DeepSeek
while keeping `model_provider = "codex-lb"`.

## What Changes

- Introduce a unified model-source abstraction that can represent existing
  subscription-backed model catalog entries and explicit OpenAI-compatible
  endpoints without turning those endpoints into accounts.
- Persist OpenAI-compatible sources with encrypted upstream API keys,
  operator-supplied model metadata, route capability flags, and health/enabled
  state.
- Extend the model catalog so `/v1/models` and eligible Codex model-picker
  catalogs can expose models from both subscription and OpenAI-compatible
  sources while preserving source identity internally.
- Extend API key scoping so keys may be restricted by model and model source,
  and route matching requests only to eligible sources.
- Reuse the existing API-key usage reservation/finalization flow for
  OpenAI-compatible sources when upstream responses provide OpenAI `usage`
  fields.
- Route OpenAI-compatible audio transcription models through
  `/v1/audio/transcriptions` when a source explicitly declares that capability.

## Non-Goals

- Do not make OpenAI-compatible endpoints `Account` rows.
- Do not apply ChatGPT/Codex account plan, usage-window, OAuth refresh,
  `chatgpt-account-id`, sticky-session owner, or websocket bridge invariants to
  OpenAI-compatible sources.
- Do not translate Chat Completions-only sources into Codex-native Responses
  streams in the initial implementation.
- Do not route continuity-sensitive Codex-native traffic to OpenAI-compatible
  sources unless the source explicitly declares Responses compatibility.

## Capabilities

### Modified Capabilities

- `model-catalog-compat`: Model catalog entries carry model-source identity and
  `/v1/models` plus eligible Codex model-picker catalogs expose
  OpenAI-compatible source models alongside subscription models.
- `api-keys`: API keys may be scoped to model sources and continue to account
  for token usage through reservations and settlement.
- `frontend-architecture`: Dashboard operators can create model sources and
  assign API keys to model sources without using account rows.
- `responses-api-compat`: Public OpenAI-compatible routes and
  Responses-capable Codex-native routes may route requests to
  OpenAI-compatible model sources when the requested model/source is eligible.
- `proxy-runtime-observability`: Request logs and diagnostics distinguish
  subscription accounts from OpenAI-compatible sources.

## Impact

- **Database**: New model-source tables and API-key source assignments.
- **Backend**: model source repository/service/schemas, model registry source
  metadata, `/v1/models` catalog merge, request routing/source selection, API-key
  scoping.
- **Security**: Store upstream source API keys encrypted, return secrets only at
  creation/update time if needed, and never log upstream API key material.
- **Testing**: Unit coverage for source metadata/registry behavior and
  integration coverage for `/v1/models`, API-key source filtering, and usage
  settlement from OpenAI-compatible responses.
