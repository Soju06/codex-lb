## Why

Remote dashboard setup requires manually configuring `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` as an environment variable before starting the server. This breaks the "one-command quick start" promise — users must know about the env var, generate a token themselves, and restart. Industry-standard tools (Grafana, GitLab, Portainer) auto-generate a bootstrap credential on first run and print it to logs.

## What Changes

- Auto-generate a cryptographically random bootstrap token on first startup when no password is configured and no manual token is set
- Print the token prominently to server logs (visible via `docker logs`)
- Wire the auto-generated token into the existing bootstrap validation path so `POST /api/dashboard-auth/password/setup` accepts it
- Clear the auto-generated token from memory after password is set
- Keep full backward compatibility with manual `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` env var
- Update frontend bootstrap screen messaging to reference server logs
- Add remote setup documentation to README (concise) and OpenSpec context docs (SoT)

## Capabilities

### New Capabilities

_(none — this extends the existing admin-auth capability)_

### Modified Capabilities

- `admin-auth`: Add auto-generated bootstrap token behavior — token generation on startup, log output, memory-only lifecycle, and updated session endpoint semantics for `bootstrap_token_configured`

## Impact

- **Backend**: New `app/core/bootstrap.py` module (~40 lines), surgical changes to `app/main.py` (startup), `app/modules/dashboard_auth/api.py` (2 call sites)
- **Frontend**: Text-only changes in `bootstrap-setup-screen.tsx` and `password-settings.tsx`
- **Documentation**: README remote setup section, `openspec/specs/admin-auth/context.md`
- **No DB changes**: Token is ephemeral (memory-only), no migration needed
- **No breaking changes**: Existing env var flow unchanged
