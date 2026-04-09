## Why

Remote dashboard setup requires manually configuring `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` as an environment variable before starting the server. This breaks the "one-command quick start" promise — users must know about the env var, generate a token themselves, and restart. Industry-standard tools (Grafana, GitLab, Portainer) auto-generate a bootstrap credential on first run and print it to logs.

## What Changes

- Auto-generate a cryptographically random bootstrap token on first startup when no password is configured and no manual token is set
- Print the token prominently to server logs (visible via `docker logs`)
- Wire the auto-generated token into the existing bootstrap validation path so `POST /api/dashboard-auth/password/setup` accepts it
- Persist the auto-generated token in shared encrypted storage so any replica can validate it
- Keep full backward compatibility with manual `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` env var
- Update frontend bootstrap screen messaging to reference server logs
- Add remote setup documentation to README (concise) and OpenSpec context docs (SoT)

## Capabilities

### New Capabilities

_(none — this extends the existing admin-auth capability)_

### Modified Capabilities

- `admin-auth`: Add auto-generated bootstrap token behavior — shared token generation on startup, log output, encrypted shared lifecycle, and updated session endpoint semantics for `bootstrap_token_configured`

## Impact

- **Backend**: New `app/core/bootstrap.py` module, surgical changes to `app/main.py`, `app/modules/dashboard_auth/api.py`, `app/modules/dashboard_auth/repository.py`, and `app/db/models.py`
- **Frontend**: Text-only changes in `bootstrap-setup-screen.tsx` and `password-settings.tsx`
- **Documentation**: README remote setup section, `openspec/specs/admin-auth/context.md`
- **DB changes**: Add encrypted shared bootstrap token storage to `dashboard_settings`
- **No breaking changes**: Existing env var flow unchanged
