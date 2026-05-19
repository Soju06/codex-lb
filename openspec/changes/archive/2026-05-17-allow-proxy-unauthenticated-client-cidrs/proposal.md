## Why
The v1.12.0 proxy-auth hardening intentionally stopped treating non-local proxy requests as implicitly trusted when API key auth is disabled. That closed a security gap, but it also broke a common Docker Desktop for Mac workflow where host-to-container traffic appears non-local to the container even when the operator is only using localhost on the host machine.

There is currently no narrow way to restore that local development path without either turning API key auth on everywhere or relaxing the shared locality rules that also gate dashboard bootstrap and authentication.

## What Changes
- Add an explicit proxy-only CIDR allowlist for unauthenticated proxy requests when API key auth is disabled.
- Apply the allowlist only to the direct socket peer IP for protected proxy routes.
- Leave `is_local_request()` and dashboard bootstrap/auth behavior unchanged.
- Add regression coverage for HTTP and websocket proxy requests plus dashboard isolation.

## Impact
- Affects `/v1/*`, `/backend-api/codex/*`, and `/backend-api/transcribe` only when API key auth is disabled.
- Restores opt-in Docker Desktop for Mac host-to-container access without reopening general remote access.
- Adds a new environment-backed configuration knob for operators who intentionally want this exception.
