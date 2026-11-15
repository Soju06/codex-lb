# Graceful Shutdown Context

See `spec.md` for normative requirements.

## Internal drain transport provenance

The start, stop and status routes retain their existing loopback-only access policy, evaluated against the launcher-captured transport peer. Forwarded client projections cannot grant this authority, and missing raw provenance fails closed before deadline parsing, state mutation or bridge callbacks.

For example, a remote raw peer projecting `127.0.0.1` receives HTTP 403, while a real loopback peer projecting a public client retains its existing drain access. A local reverse proxy is still a loopback transport peer; this rule does not independently authenticate requests that proxy forwards to internal paths. Authorized reversible drain, committed shutdown deadlines and response schemas are unchanged.
