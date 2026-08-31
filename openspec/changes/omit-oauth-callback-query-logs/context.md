# OAuth callback privacy context

## Purpose

The browser OAuth redirect carries two temporary secrets in its query: a single-use authorization code and an anti-CSRF state token. The callback listener is local, but its stderr/stdout can still be collected by containers, supervisors, or developer tooling, so loopback transport does not make those values safe to record.

## Decision

Suppress the callback-only runner's generic access record before it is created. This boundary control is intentionally preferred over formatter redaction: formatters should never receive the raw callback target, and text and JSON modes inherit the same guarantee.

## Constraints

- The callback stays on the same path and invokes the same handler.
- OAuth exchange, state validation, response HTML, and callback-server lifecycle remain unchanged.
- Global application and proxy access logging remain unchanged.
