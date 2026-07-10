## Context

codex-lb already serves the Codex-native image routes under the provider
`base_url`, but the documented custom-provider configuration does not include
the actor-authorization marker used by Codex's image-tool eligibility checks.
As a result, the client can report the built-in `imagegen` tool as unsupported
before any image request reaches codex-lb.

## Goals / Non-Goals

**Goals:**

- Make every copyable Codex provider example image-generation capable.
- Explain why the static header exists and avoid presenting it as a secret.
- Keep the English and Chinese setup instructions equivalent.

**Non-Goals:**

- Change codex-lb authentication or authorize requests based on this header.
- Add another image endpoint or alter image request/response handling.
- Modify users' existing `~/.codex/config.toml` files automatically.

## Decisions

- Put the inline `http_headers` map inside `[model_providers.codex-lb]` in both
  the default and API-key examples. This is the Codex-supported provider scope
  and avoids introducing a second configuration form.
- Use the stable value `codex-lb`. Codex requires a non-empty actor marker; a
  recognizable constant makes clear that this is not a credential. An
  environment-backed value was considered but rejected because it would imply
  secret rotation and add setup with no security benefit.
- Document the gateway reason immediately after the primary configuration
  block, including the instruction for existing users. A code comment alone is
  too easy to miss when users patch an existing configuration.
- Keep codex-lb runtime behavior unchanged. The header is interpreted by the
  Codex client and is harmless extra request metadata at the proxy boundary.

## Risks / Trade-offs

- [Codex changes its eligibility rules] -> Pin the verified upstream commit in
  context and keep the explanation about observable purpose rather than
  internal implementation details.
- [Existing users copy only the API-key block] -> Include the header in that
  complete provider example as well as the primary example.
- [Users mistake the marker for authorization at codex-lb] -> State explicitly
  that it is not a credential and does not replace Bearer API-key auth.

## Migration Plan

Publish the corrected setup examples. Existing users add the single
`http_headers` entry to their `codex-lb` provider table and start a new Codex
session so client capabilities are recalculated. Rollback is documentation-only
and consists of reverting the examples.

## Open Questions

None.
