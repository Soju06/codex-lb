# Change: Validate traffic failure-path parity

## Why

The traffic parity toolkit proves successful HTTP/SSE/WebSocket fidelity, but
its controlled origin cannot deterministically produce rate limits, upstream
5xx responses, delayed headers, truncated streams, or WebSocket rejection and
mid-turn closure. The capture addon also writes only completed HTTP exchanges,
so a timeout or connection failure can disappear from Path C and look like a
missing capture rather than an observed transport outcome.

## What Changes

- Add opt-in, deterministic failure scenarios to the loopback-only controlled
  origin while keeping success as the default.
- Capture targeted HTTP transport failures using privacy-safe error categories
  without persisting exception messages, peer addresses, or credentials.
- Report status, retry hints, terminal class, completeness, and network-error
  category as an explicit B/C failure-outcome comparison.
- Keep incomplete turns as strict mismatches; the failure section explains the
  observed translation but does not weaken the existing success-fidelity gate.
- Document a repeatable matrix for 429, 503, timeout, incomplete SSE,
  WebSocket rejection, and incomplete WebSocket scenarios.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: controlled origin fixture, mitmproxy addon, turn analyzer,
  report generator, tests, and traffic-parity operator documentation
