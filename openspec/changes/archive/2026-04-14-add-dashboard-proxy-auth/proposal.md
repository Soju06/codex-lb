## Why

Deployments that already sit behind Authelia or another reverse proxy still need a local dashboard password today unless operators fully expose the dashboard in unauthenticated mode. That leaves an awkward gap between secure passwordless SSO at the edge and codex-lb's built-in password/TOTP model.

## What Changes

- Add an env-configured dashboard auth mode switch with `standard`, `trusted_header`, and `disabled` modes.
- Allow reverse-proxy-authenticated dashboard access via a trusted header only when the request comes from configured trusted proxy CIDRs.
- Keep password/TOTP available as an optional fallback in trusted-header mode, but fail closed when no proxy header or fallback password is present.
- Add a fully disabled dashboard auth mode for controlled Docker or internal-network deployments.
- Document the new modes in `.env.example` and `README.md`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `admin-auth`: support trusted reverse-proxy dashboard auth and explicit auth bypass mode without weakening existing password/TOTP behavior.

## Impact

- Code: `app/core/auth/*`, `app/core/config/settings.py`, `app/modules/dashboard_auth/*`, `frontend/src/features/auth/*`, `frontend/src/features/settings/*`
- Tests: dashboard auth integration tests, frontend auth/settings tests, settings validation tests
- Docs: `.env.example`, `README.md`, OpenSpec admin-auth delta
