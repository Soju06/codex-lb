# Native transport for direct usage queries

## Why

Account-routed usage already uses CodexClient's native transport, but direct
usage GETs always use Python. Move this one read-only call to the existing
helper while preserving caller ownership and the usage response contract.

## What Changes

- Prefer native transport for authorized direct usage GETs without an injected
  Python client. Keep routing, retry policy and payload validation in Python.
- Permit missing-helper fallback only before the first native attempt; preserve
  protocol failures, response-body failures, cancellation and peer isolation.
- Match direct Python status retry timing and preserve non-JSON error bodies.
- Add real-helper loopback probes to the Rust CI job and document ownership.

## Impact

Affected capability: `outbound-http-clients`. No new settings, endpoints,
database changes or dependencies. Credit consumption, routed usage policy,
OAuth, and other outbound clients keep their current behavior.
