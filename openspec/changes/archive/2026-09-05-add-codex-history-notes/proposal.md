# Add Codex history and notes backend routes

## Why
Codex 0.153.1 exposes experimental context tools whose backend POST routes currently return 405 through codex-lb. These account-scoped operations cannot safely use unrestricted account rotation.

## What Changes
- Add the ten explicit Codex history/notes v2 routes, including the startup thread hint.
- Forward opaque bodies, query parameters and encryption/truncation headers through the existing control transport.
- Require an authenticated proxy key scoped to exactly one account, preserving existing routing and refresh policies inside that scope.
- Redact private payloads and upstream error details from logs and public errors.
- Document an isolated client profile and the limits of the initial implementation.

## Impact
New routes only; no database migration, required environment variable, default routing change or deployment of the running proxy. Multi-account history ownership and migration are deferred. Existing keys and sessions are unchanged.
