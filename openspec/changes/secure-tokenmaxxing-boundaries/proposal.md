## Why

The Onda deployment places the administrative dashboard behind Cloudflare
Access and exposes fleet capacity to a native client. The current trusted-header
mode accepts identity based only on proxy network provenance, which is not a
sufficient cryptographic authentication boundary, and the current fleet summary
does not express a safe normalized utilization headline. Production request-log
retention also needs an enforceable upper bound.

## What Changes

- Add fail-closed Cloudflare Access JWT validation to trusted-header dashboard
  authentication and derive the actor from the validated email claim.
- Add normalized five-hour and weekly utilization, freshness, inclusion, and
  additional-quota fields to `GET /api/fleet/summary`.
- Add automatic hard deletion of request logs older than a configurable
  retention period, with a 30-day production setting.
- Keep payload and conversation archival disabled in the production contract.

## Impact

- Trusted-header deployments can require exact Access issuer, audience, expiry,
  and email-domain claims instead of trusting a forgeable identity header.
- Existing fleet consumers remain compatible because summary fields are added
  without removing or changing current fields.
- Request-level metadata has a bounded lifetime; no prompt or response bodies
  are retained by the Onda deployment.
