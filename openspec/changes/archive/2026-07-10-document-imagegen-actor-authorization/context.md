# Context: document-imagegen-actor-authorization

## Purpose and scope

The Codex-native image generation and edit aliases are already implemented by
`add-codex-images-route-alias`. This change closes the remaining onboarding gap:
Codex client gateways need an actor-authorization marker in the custom model
provider configuration before the built-in `imagegen` surface is considered
eligible in all supported client paths.

The scope is client configuration guidance. codex-lb does not trust this marker
for authentication and does not need to interpret its value.

## Verified upstream behavior

The behavior was checked against `openai/codex` commit
`0d1733b5e9ea027a0ff9d75cc3e11103f045f1ce` (2026-07-10):

- `ModelProviderInfo` recognizes a case-insensitive, non-empty
  `x-openai-actor-authorization` entry in the provider's `http_headers` map for
  custom providers that do not use first-party OpenAI auth
  (`codex-rs/model-provider-info/src/lib.rs:36,400-408`).
- Image-tool planning accepts either the actor-authorization path or a provider
  using current Codex backend auth
  (`codex-rs/core/src/tools/spec_plan.rs:372-386`).
- The image-generation extension also considers actor-authorized custom
  providers when registering the built-in tool
  (`codex-rs/ext/image-generation/src/extension.rs:36-45`).
- Codex's published configuration schema defines provider `http_headers` as a
  string-to-string map, so the inline TOML table is a supported configuration
  shape: <https://developers.openai.com/codex/config-reference/>.

## Decision rationale and constraints

The configuration uses:

```toml
http_headers = { "x-openai-actor-authorization" = "codex-lb" }
```

Only a non-empty marker is required for client capability classification, so a
fixed project identifier is clearer than a token-shaped value. It must not be
described as a secret, access token, or substitute for codex-lb API-key auth.
When API-key auth is enabled, `env_key = "CODEX_LB_API_KEY"` remains the source
of the Bearer credential.

The entry belongs in every complete `[model_providers.codex-lb]` example.
Documenting it only as an optional troubleshooting step would leave fresh
installations unable to rely on the advertised image capability.

The standard codex-lb setup also keeps `requires_openai_auth = true` for Codex
app compatibility. The explicit marker covers client gateway paths that
classify the custom provider before the core runtime checks; it does not replace
the separate first-party-auth eligibility path.

## Failure modes and operations

- Without the header, Codex can hide or reject `imagegen` locally; codex-lb
  receives no `/backend-api/codex/images/*` request, so proxy logs cannot
  diagnose the missing client capability.
- A blank header value is not a valid actor marker.
- Adding the header to a running client may not update an existing thread's
  cached provider/tool state. Start a new Codex session after editing
  `~/.codex/config.toml`.
- The header does not grant access to codex-lb. Deployments with API-key auth
  continue to require the configured Bearer token, which codex-lb validates
  independently before it replaces inbound credentials with account-scoped
  upstream authentication.

## Concrete user flow

1. Add the `http_headers` line inside `[model_providers.codex-lb]`.
2. Keep `env_key = "CODEX_LB_API_KEY"` when the deployment requires API keys.
3. Start a new Codex CLI or IDE session.
4. Invoke the built-in `imagegen` tool; Codex sends generation or edit requests
   below the configured `base_url`, where codex-lb's existing aliases handle
   them.

The archived delta contract is in `specs/images-api-compat/spec.md`; its synced
normative requirement is in `openspec/specs/images-api-compat/spec.md`.
