## Why

Peer fallback currently forwards the caller's local API key to the selected peer. Operators need peer fallback requests to authenticate to peer `codex-lb` instances with the process-level `CODEX_API_KEY` credential instead, so peer instances do not need to share the same local API key database.

## What Changes

- Add `CODEX_API_KEY` as the canonical process-level credential for outbound peer fallback authentication.
- When `CODEX_API_KEY` is configured, replace the forwarded `Authorization` header on peer fallback requests with `Bearer <CODEX_API_KEY>`.
- Keep API-key-scoped peer URL selection unchanged.
- Preserve the existing behavior when `CODEX_API_KEY` is unset.

## Impact

- Runtime proxy: peer fallback outbound headers change when `CODEX_API_KEY` is present.
- Configuration: `.env.example` documents `CODEX_API_KEY`.
- Tests: peer fallback header forwarding and settings parsing coverage need updates.
