## Why

`codex-lb` currently assumes a ChatGPT-web upstream for all routed traffic. The proxy stores ChatGPT OAuth credentials, refreshes them through `auth.openai.com`, and forwards requests to `chatgpt.com/backend-api`. That works for pooled ChatGPT accounts, but it cannot honestly represent or safely route the OpenAI Platform API-key path that current Codex clients and OpenAI-compatible SDKs can use against the public Responses API.

We need a provider-aware upstream design so operators can keep existing ChatGPT-web pooling as the primary path while also adding a single OpenAI Platform API key as a controlled fallback when the compatible ChatGPT pool is no longer healthy under the configured primary and secondary drain thresholds, allowing the public API contract to take over only for supported stateless routes.

## What Changes

- Add a first-class upstream provider design alongside the current ChatGPT-web mode.
- Split persistence so existing ChatGPT OAuth-backed `accounts` remain intact while OpenAI Platform credentials are stored as a separate provider-managed upstream identity type.
- Introduce a provider-aware routing subject abstraction so selection, sticky mappings, and request logging no longer assume every upstream target is a ChatGPT account.
- Route phase-1 public OpenAI-compatible HTTP endpoints through provider-specific transports while preserving current ChatGPT-web behavior as the default path and using Platform only as fallback for `/v1/models` and stateless HTTP `/v1/responses`.
- Define an explicit `codex-lb` phase-1 support matrix covering route family, transport, compact behavior, continuity-dependent request shapes, and wire-level Platform auth behavior.
- Make mixed-provider deployments explicit and fail closed: Platform identities are opt-in for eligible public route families, never silently used for ChatGPT-private or continuity-dependent behavior, cannot be operated standalone, and are limited to a single registered API key.
- Add dashboard UX and operational contracts for creating, validating, listing, and observing provider-specific upstream identities.

## Capabilities

### Modified Capabilities

- `responses-api-compat`
- `sticky-session-operations`
- `proxy-runtime-observability`

### Added Capabilities

- `upstream-provider-management`
