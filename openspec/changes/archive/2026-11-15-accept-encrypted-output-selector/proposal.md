## Why

Codex desktop requests containing `include: ["output.encrypted_content"]` fail validation before dispatch. Upstream issue Soju06/codex-lb#2684 and PR #2682 identify a missing selector in the shared Responses allowlist.

## What Changes

- Accept and preserve the encrypted-output selector on native and public Responses HTTP and WebSocket requests.
- Keep existing selectors, omitted/empty include arrays, and invalid-selector rejection unchanged.
- Add request-model and transport regression coverage.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: encrypted-output inclusion.

## Impact

One allowlist entry, regression tests, and OpenSpec. No new settings or dependencies.
