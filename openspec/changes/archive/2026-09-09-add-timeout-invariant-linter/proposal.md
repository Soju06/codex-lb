## Why

Timeout and TTL mismatches have repeatedly caused healthy codex-lb work to be
killed by a different, shorter budget. The project needs those relationships
encoded as executable configuration policy instead of scattered comments.

## What Changes

- Add a declarative timeout-invariant rule table over effective Settings fields.
- Validate the effective startup configuration in non-strict mode by default,
  logging CRITICAL for every violated rule.
- Add `timeout_invariant_validation_strict` for deployments that want startup to
  fail on violations.
- Add a strict CI entrypoint via `python -m app.core.timeout_invariants`.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `deployment-installation`: defines startup and CI validation for timeout
  invariants.
- `proxy-runtime-observability`: defines low-cardinality CRITICAL diagnostics
  for timeout-invariant violations.

## Impact

- Code: `app/core/timeout_invariants.py`, startup lifespan settings validation,
  and the Settings strict-mode flag.
- Tests: focused unit coverage for defaults, inverted config detection, strict
  raise, and the CI entrypoint.
- Operators: default deployments continue to start; strict mode opts into
  fail-fast behavior.
