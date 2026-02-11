## 1. OpenSpec update

- [x] 1.1 Add `dashboard-auth` capability requirement for setup token header transport from dashboard UI

## 2. UI implementation

- [x] 2.1 Prompt setup token on demand during TOTP setup flows
- [x] 2.2 Send `X-Codex-LB-Setup-Token` for TOTP setup start/confirm requests

## 3. Validation

- [ ] 3.1 Run dashboard auth integration tests (blocked in this checkout: `async_client` fixture is missing)
- [ ] 3.2 Run `openspec validate --specs` (blocked in this environment: `openspec` CLI not installed)
