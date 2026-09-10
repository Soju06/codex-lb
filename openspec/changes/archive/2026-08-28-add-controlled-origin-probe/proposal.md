# Change: Add controlled Responses origin probe

## Why

The traffic analyzer can now distinguish an intercept observer from a real
origin observer, but the repository has no deterministic origin that accepts
all three Responses transports. Operators therefore cannot collect a public
source-address observation without directing test credentials and traffic to
an ad-hoc service with unknown behavior.

## What Changes

- Add an explicitly launched, deterministic Responses fixture that supports
  model discovery, HTTP JSON, HTTP SSE, and persistent WebSocket turns.
- Keep the fixture loopback-only by default and require an explicit override
  for a non-loopback bind.
- Place a TLS/HTTP-aware reverse capture boundary in front of the fixture so
  the existing addon records the real socket peer with observer role `origin`.
- Document separate A and C runs against the same controlled observation
  boundary, including certificate, credential, and exposure safeguards.
- Keep the fixture independent of OpenAI and never reflect prompts,
  credentials, or client-provided content in its responses.

## Impact

- Affected spec: `compatibility-tooling`
- Affected code: traffic-analysis origin fixture, focused tests, and operator
  documentation
