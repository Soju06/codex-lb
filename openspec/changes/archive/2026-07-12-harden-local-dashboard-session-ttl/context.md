## Migration

Alembic revision `20260705_000000_harden_dashboard_session_ttl` changes the `dashboard_settings.dashboard_session_ttl_seconds` server default to `31536000` and updates existing rows only when they still equal the legacy default `43200`.

## Guardrail

The 1-year effective TTL is intentionally limited to standard dashboard auth mode when the request is socket-level local. Localhost-published Docker deployments where the container sees a bridge peer can opt in with `CODEX_LB_DASHBOARD_TRUST_LOOPBACK_HOST_HEADER_FOR_LONG_SESSIONS=true`; that override still requires a loopback dashboard URL and no forwarded-client headers. Requests that arrive through proxy-aware mode, trusted-header auth, non-loopback hosts, or the default bridge-without-override path receive the 12-hour fallback when the configured value exceeds 30 days.
