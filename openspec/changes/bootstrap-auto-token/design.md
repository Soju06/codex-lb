## Context

Remote dashboard bootstrap currently requires `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` to be set as an env var before first run. Two call sites read it: the session endpoint (to report `bootstrap_token_configured`) and the `setup_password` endpoint (to validate the submitted token). The token is only needed once — during initial password setup from a remote client. Local (localhost) access bypasses bootstrap entirely.

## Goals / Non-Goals

**Goals:**
- Auto-generate a bootstrap token on first startup when no password exists and no manual token is set
- Print it to server logs so users can copy from `docker logs`
- Wire it into the existing validation path with zero behavioral change for existing env var users
- Clear the token from memory after password is set
- Update frontend messaging to reference server logs
- Document the flow in README and OpenSpec context docs

**Non-Goals:**
- Persisting auto-generated tokens to DB or disk
- Adding a web-based token display (security risk)
- Changing the localhost bypass behavior
- Modifying the TOTP or password hashing flow

## Decisions

**D1: New module `app/core/bootstrap.py` for token state**

Module-level `_auto_generated_token: str | None` with three functions: `get_active_bootstrap_token()`, `maybe_generate_bootstrap_token()`, `clear_auto_generated_token()`. Follows `app/core/startup.py` pattern (module-level state + accessor functions).

Alternative: Store on the Settings object → rejected because token is ephemeral and env-level, not a DB-persisted setting.

**D2: `secrets.token_urlsafe(32)` for generation**

44-character URL-safe base64 string (256 bits entropy). Same stdlib used across Python ecosystem for one-time tokens.

Alternative: UUID4 → rejected — lower entropy per character, less standard for security tokens.

**D3: Token printed via `logger.info()` with visual delimiters**

Multi-line log with `====` borders for visibility in `docker logs` output. Uses the existing `logger` in `main.py`.

Alternative: `print()` → rejected — bypasses log configuration and formatting.

**D4: Priority chain — env var > auto-generated > None**

`get_active_bootstrap_token()` checks env var first (via `get_settings().dashboard_bootstrap_token`), then falls back to `_auto_generated_token`. If env var is set, auto-generation never fires.

**D5: Token cleared after password setup, not on explicit endpoint**

`clear_auto_generated_token()` called inside `setup_password()` after successful password storage. Token becomes stale in memory but harmless since the password setup endpoint rejects duplicate setups (409 Conflict).

## Risks / Trade-offs

**R1: Token visible in logs** → By design. Same pattern as Grafana/GitLab/Portainer. Mitigated by: token is one-time (useless after password set), `docker logs` requires container access.

**R2: Token regenerates on each restart** → Intentional. Prevents stale tokens from accumulating. Users must re-check logs if they restart before setting a password.

**R3: Race condition on clear** → Not a real risk. Single-threaded async — `clear_auto_generated_token()` runs in the same request that sets the password. No concurrent access issue.
