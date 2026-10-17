# Change: Automate the versioned traffic canary

## Why

The parity toolkit now produces a complete offline verdict, but a Codex CLI
upgrade can invalidate the retained wire profile before an operator notices.
The current client already moved from the attested 0.150.1 to 0.151.0. Failure
scenarios also remain separate reports, so their end-to-end A/B compatibility
is not yet one fail-closed matrix result.

## What Changes

- Add a compact failure-matrix gate for success, HTTP 429/503, timeout,
  incomplete SSE, WebSocket rejection, and incomplete WebSocket recovery.
- Allow the composite gate to require that failure-matrix section.
- Add a lock-safe canary runner that runs on a Codex version change or after a
  bounded weekly interval and advances state only after success.
- Install a user timer on this host for daily version checks and weekly
  execution, backed by the isolated authenticated probe environment.
- Keep the expensive repeated TLS distribution as an explicitly separate full
  attestation; the fast scheduled suite MUST NOT claim full composite parity.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: traffic-analysis gates, canary runner, tests, runbook, and
  host-local user service/timer configuration
- Runtime proxy behavior and public APIs are unchanged.
