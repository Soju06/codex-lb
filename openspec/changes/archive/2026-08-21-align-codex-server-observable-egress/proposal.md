## Why

Live A/C captures show that a server can distinguish direct Codex traffic from codex-lb traffic before considering payload semantics. Model discovery currently exposes aiohttp defaults instead of a Codex identity, and the HTTP session bridge can select an upstream WebSocket before the configured downstream-HTTP transport policy is applied.

## What Changes

- Give subscription model-discovery requests the same first-party Codex identity header family used by proxied requests and remove the aiohttp `Accept` default mismatch.
- Apply the effective downstream-HTTP transport policy, including per-key overrides and explicit transport precedence, before enabling the HTTP-to-WebSocket session bridge.
- Add server-observable parity assertions to the traffic comparison report so protocol, TLS, and header regressions remain independently visible.
- Define a native Rust egress boundary for the remaining HTTP/2 and rustls fingerprint gap without silently claiming parity from header-only changes.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: HTTP bridge admission obeys the same transport precedence contract as the ordinary streaming retry path.
- `compatibility-tooling`: Codex/control identity and server-observable transport mismatches are reported as separate parity dimensions.

## Impact

Model discovery headers, HTTP bridge admission, focused proxy tests, and traffic-parity reporting are affected. Public API shapes and zero-configuration startup are unchanged. The native egress boundary is internal and does not add a required setting or deployment step.
