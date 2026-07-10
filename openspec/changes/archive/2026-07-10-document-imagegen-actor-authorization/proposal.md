## Why

Codex clients configured with the custom `codex-lb` model provider can hide or
reject the built-in `imagegen` tool before it reaches codex-lb, even though the
Codex-native image routes are implemented. The setup contract must include the
actor-authorization marker that Codex uses for eligible custom providers.

## What Changes

- Add `x-openai-actor-authorization = "codex-lb"` to every documented Codex
  provider configuration, including API-key-authenticated setups.
- Explain that the header enables Codex's built-in image-generation gateway and
  that its static value is a capability marker rather than a credential.
- Record the verified upstream Codex detection behavior and the failure mode for
  configurations that omit the header.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `images-api-compat`: Require Codex client setup guidance to include the actor
  authorization header needed to expose the built-in image-generation tool.

## Impact

- User-facing Codex CLI and IDE setup examples in `README.md` and
  `README.zh-CN.md`.
- OpenSpec image compatibility requirements and context.
- No proxy runtime, API, dependency, or schema changes.
