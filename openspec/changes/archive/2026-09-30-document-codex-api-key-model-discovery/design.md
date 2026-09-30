## Context

A catalog endpoint returning a model does not prove that a client fetches it.
In Codex 0.159.0, a configured provider API key takes the API-key discovery
path. That path requires both `features.api_key_model_discovery` and catalog
support; a custom `base_url` needs an explicit `model_catalog_url` to satisfy
the latter.

## Goals / Non-Goals

Goals:
- Keep the inline guide and downloadable example consistent.
- Discover models through the native catalog for ordinary and opt-in providers.
- Explain the verified client behavior without changing authentication.

Non-goals:
- Change server catalog construction, routing, or model visibility.
- Change the default model or add settings to users' existing installations.
- Claim model inference or older-client compatibility from a discovery test.

## Decisions

Set `api_key_model_discovery = true` in the existing example's feature table
and set `model_catalog_url` to the same origin as each provider's `base_url`,
with the `/backend-api/codex/models` path.

Keep `requires_openai_auth`, API-key handling, and the opt-in capability header
unchanged. The catalog URL does not replace the Responses API base URL.

Verify the shipped TOML through Codex's real app-server `model/list` endpoint
in an isolated client home. Change only the deployment address and supply an
existing API key at the probe boundary; do not hard-code credentials or local
machine details in repository artifacts.

## Risks / Trade-offs

The discovery feature is client-version-dependent. Describe the behavior as
verified with Codex 0.159.0 rather than promising it for every Codex release.
Users with an existing `[features]` or provider table must merge the settings
into that table instead of duplicating TOML tables.

## Validation

- Parse the downloadable example and relevant inline TOML blocks.
- Run the existing config-consuming provider regression tests.
- Observe an advertised model absent from the bundled catalog in the real
  app-server response, including its `hidden` flag.
- Build the documentation and strictly validate OpenSpec.
