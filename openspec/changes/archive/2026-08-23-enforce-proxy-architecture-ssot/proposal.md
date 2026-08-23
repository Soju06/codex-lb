## Why

The required proxy architecture checker currently passes even though
`service.py` and `load_balancer.py` exceed the limits in the normative
`proxy-architecture` specification. The checker duplicated those limits as
independently editable constants, so later ratchet increases silently broke the
OpenSpec contract and removed the intended CI protection.

## What Changes

- Restore `service.py` to at most 2,600 lines and `load_balancer.py` to at most
  3,021 lines through focused, behavior-neutral extraction into their existing
  private implementation domains; keep `LoadBalancer.select_account()` within
  its 527-line limit.
- Make the architecture checker consume an OpenSpec-owned machine-readable
  ratchet definition instead of maintaining a second set of numeric limits.
- Add regression coverage proving that the checker follows the OpenSpec-owned
  values, rejects invalid ratchet definitions, and fails the real repository
  tree when any normative limit is exceeded.
- Preserve proxy APIs, routing and selection semantics, compatibility imports,
  persistence, configuration, and user-visible behavior.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `proxy-architecture`: Require architecture ratchets to have one OpenSpec-owned
  definition and require the lint checker to fail closed when that definition
  is missing, invalid, or violated.

## Impact

- Affected implementation: proxy façade helpers, focused private
  load-balancer/service modules, and `scripts/check_proxy_architecture.py`.
- Affected verification: architecture-check unit tests, public proxy façade and
  load-balancer characterization tests, and focused proxy integration coverage.
- No public API, wire contract, routing policy, database schema, setting,
  deployment, or dashboard change.
