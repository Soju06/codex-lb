# Images API compatibility context

## Purpose and scope

codex-lb exposes OpenAI-compatible `/v1/images/*` endpoints and Codex-native
aliases below `/backend-api/codex/images/*`. The native aliases let Codex's
built-in `$imagegen` tool use the same account selection, validation, usage
accounting, and response pipeline as other image clients.

Codex client setup is part of this compatibility boundary. A working server
route is insufficient when a custom-provider gateway hides the image tool
before it makes an HTTP request.

## Codex provider contract

Every complete `[model_providers.codex-lb]` example includes:

```toml
base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
http_headers = { "x-openai-actor-authorization" = "codex-lb" }
```

The fixed `codex-lb` value is a client capability marker, not a credential.
Deployments that enable codex-lb API-key authentication still configure
`env_key = "CODEX_LB_API_KEY"`; codex-lb validates that Bearer credential
independently.

## Verified upstream behavior

This contract was checked against `openai/codex` commit
[`0d1733b5e9ea027a0ff9d75cc3e11103f045f1ce`](https://github.com/openai/codex/commit/0d1733b5e9ea027a0ff9d75cc3e11103f045f1ce)
(2026-07-10):

- Codex's provider model defines
  `x-openai-actor-authorization` as its actor marker and recognizes a
  case-insensitive, non-empty `http_headers` entry for custom providers that do
  not use first-party OpenAI auth
  ([source](https://github.com/openai/codex/blob/0d1733b5e9ea027a0ff9d75cc3e11103f045f1ce/codex-rs/model-provider-info/src/lib.rs#L400-L408)).
- Image-tool planning accepts either that actor-authorized provider path or a
  provider using current Codex backend auth
  ([source](https://github.com/openai/codex/blob/0d1733b5e9ea027a0ff9d75cc3e11103f045f1ce/codex-rs/core/src/tools/spec_plan.rs#L372-L386)).
- The standalone image extension applies the corresponding provider eligibility
  checks before registering its tool
  ([source](https://github.com/openai/codex/blob/0d1733b5e9ea027a0ff9d75cc3e11103f045f1ce/codex-rs/ext/image-generation/src/extension.rs#L36-L45)).
- The published Codex configuration schema supports provider `http_headers` as
  a string-to-string map in `config.toml`
  ([configuration reference](https://developers.openai.com/codex/config-reference/)).

The standard codex-lb setup also keeps `requires_openai_auth = true` for Codex
app compatibility. The explicit actor marker remains necessary for client
gateway paths that classify the custom provider before the core runtime checks.

## Constraints and security boundary

- The marker MUST NOT be described as authentication accepted by codex-lb.
- The marker does not replace `Authorization: Bearer ...` when API-key auth is
  enabled. codex-lb strips the inbound Authorization value before constructing
  upstream account credentials, so the two concerns remain separate.
- The header value must be non-empty. No secret storage or rotation mechanism is
  appropriate for the static `codex-lb` value.
- The provider `base_url` remains the Codex base, not `/v1`, because the built-in
  image client joins `images/generations` and `images/edits` directly onto it.

## Failure modes and operations

- Without the marker, affected Codex gateways report image generation as
  unsupported and codex-lb receives no image-route request. Server-side route
  logs therefore cannot diagnose this client-side failure.
- Adding the line may not update capability state cached by an existing thread.
  Start a new Codex session after editing `~/.codex/config.toml`.
- A missing or invalid codex-lb API key still produces the normal proxy
  authentication error even when the actor marker is present.
- Route-level validation and upstream errors remain governed by the normative
  requirements in [spec.md](./spec.md).

## Example user flow

1. Add the actor marker inside `[model_providers.codex-lb]`.
2. Preserve `env_key = "CODEX_LB_API_KEY"` when the deployment requires it.
3. Start a new CLI or IDE session and invoke `$imagegen`.
4. Codex posts to `/backend-api/codex/images/generations` or
   `/backend-api/codex/images/edits`; codex-lb handles the request through the
   existing Images compatibility pipeline.
