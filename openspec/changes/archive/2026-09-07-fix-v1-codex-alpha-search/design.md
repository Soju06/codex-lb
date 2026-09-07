## Context

Standalone search is already implemented by `codex_alpha_search` and the Codex
control service. Production clients using a `/v1` base URL reach the SPA fallback
because the corresponding POST route is missing. Evidence is in `context.md`.

## Goals / Non-Goals

- Make standalone search work with both supported base URL prefixes.
- Keep authentication, account scope, capability guards, and forwarding shared.
- No search schema, new settings, transport changes, or client setup changes.

## Decisions

Add a `v1_router.post` decorator to the existing handler. Both routers already
use identical proxy authentication and OpenAI error-format dependencies, and the
handler applies the existing required-capability guard before control forwarding.
Registering a route avoids rewriting unrelated v1 control endpoints.

Retain current trailing-slash rejection to match the canonical endpoint. The
existing path-rewrite middleware handles the doubled Codex/v1 prefix.

## Risks / Trade-offs

An alias could accidentally bypass authentication or capability restrictions.
Public-route regressions exercise missing and invalid credentials, a scoped API
key, opaque forwarding, normalized errors, and the capability route inventory.

## Validation

Reproduce the v1 route failure before adding the decorator. Run route and control
integration tests after the fix, then lint and strict OpenSpec validation. Runtime
rollout is a separate operator deployment step.
