## Context

Remote dashboard bootstrap currently requires `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` to be set as an env var before first run. Two call sites read it: the session endpoint (to report `bootstrap_token_configured`) and the `setup_password` endpoint (to validate the submitted token). The token is only needed once — during initial password setup from a remote client. Local (localhost) access bypasses bootstrap entirely.

## Goals / Non-Goals

**Goals:**
- Auto-generate a bootstrap token on first startup when no password exists and no manual token is set
- Print it to server logs so users can copy from `docker logs`
- Wire it into the existing validation path with zero behavioral change for existing env var users
- Make the token valid across replicas and restarts until password setup succeeds
- Update frontend messaging to reference server logs
- Document the flow in README and OpenSpec context docs

**Non-Goals:**
- Adding a web-based token display (security risk)
- Changing the localhost bypass behavior
- Modifying the TOTP or password hashing flow

## Decisions

**D1: Shared hashed token in `dashboard_settings`**

Persist a SHA-256 hash of the auto-generated bootstrap token in `DashboardSettings.bootstrap_token_hash` as shared DB state. `has_active_bootstrap_token()` and `validate_bootstrap_token()` read the shared state directly from DB (not `SettingsCache`) so cross-replica bootstrap is not delayed by per-process TTL. `ensure_auto_bootstrap_token()` returns plaintext when it creates a new token or rotates an existing stored hash during restart recovery.

Alternative: module-global in-memory token → rejected because Helm defaults are multi-replica and the token must validate across pods.

**D2: `secrets.token_urlsafe(32)` for generation**

44-character URL-safe base64 string (256 bits entropy). Same stdlib used across Python ecosystem for one-time tokens.

Alternative: UUID4 → rejected — lower entropy per character, less standard for security tokens.

**D3: Token printed via `logger.info()` with visual delimiters**

Multi-line log with `====` borders for visibility in `docker logs` output. Uses the existing `logger` in `main.py`.

Alternative: `print()` → rejected — bypasses log configuration and formatting.

**D4: Priority chain — env var > shared token hash > None**

Bootstrap validation checks env var first (via `get_settings().dashboard_bootstrap_token`), then falls back to the shared token hash stored in `dashboard_settings`. If env var is set, the shared auto-generated token hash is ignored and cleared on startup.

**D5: Token cleared atomically with password setup**

`DashboardAuthRepository.try_set_password_hash()` now clears `bootstrap_token_encrypted` in the same UPDATE that sets `password_hash`. That guarantees one successful setup consumes the shared token across all replicas.

## Risks / Trade-offs

**R1: Token visible in logs** → By design. Same pattern as Grafana/GitLab/Portainer. Mitigated by: token is one-time (useless after password set), `docker logs` requires container access.

**R2: Restart recovery rotates the token** → Intentional. Because only the hash is stored, a restarting passwordless replica rotates to a fresh token and logs it so remote setup stays recoverable without reusing an unrecoverable plaintext token.

**R3: Password removal re-bootstrap** → After an authenticated password removal, the API immediately recreates and logs a new bootstrap token so remote-only operators do not need a restart.

**R4: Shared-state race on generation/consumption** → Mitigated by atomic `UPDATE ... WHERE ... IS NULL` guards in the repository. One replica wins generation, one successful setup consumes the token.
