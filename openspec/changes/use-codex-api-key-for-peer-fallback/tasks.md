## 1. Runtime

- [x] 1.1 Add a `CODEX_API_KEY` setting for outbound peer fallback authentication.
- [x] 1.2 Override peer fallback request `Authorization` with `Bearer <CODEX_API_KEY>` when configured.
- [x] 1.3 Preserve original header forwarding when `CODEX_API_KEY` is unset.

## 2. Verification

- [x] 2.1 Add unit coverage for `CODEX_API_KEY` settings parsing.
- [x] 2.2 Add unit coverage for peer fallback authorization override.
- [x] 2.3 Run relevant backend tests and OpenSpec validation.
