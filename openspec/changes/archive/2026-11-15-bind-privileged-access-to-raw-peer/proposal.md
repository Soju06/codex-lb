## Why

Trusted-header dashboard identity and the internal drain routes authorize the forwarded client projection instead of the launcher-captured transport peer. A supported broad `FORWARDED_ALLOW_IPS` policy can therefore let an untrusted remote peer present a trusted or loopback address, while a legitimate trusted proxy forwarding a public client can be rejected. The existing raw-peer helper already protects other authorization paths (upstream PRs #2632 and #2633).

## What Changes

- Bind identity-header retention and trusted-header authentication to the captured raw socket peer and existing trusted-proxy CIDRs.
- Share a raw-peer loopback guard across internal drain start, stop and status; reject unavailable provenance before parsing deadlines or calling state/bridge operations.
- Preserve forwarded client address/scheme attribution, identity cardinality and groups, password fallback, drain state/deadline contracts and response schemas.
- Add composed middleware/HTTP regressions for IPv4/IPv6, spoofed projections, ordinary trusted proxies, missing capture and rejection before side effects.

## Capabilities

### Modified Capabilities

- `admin-auth`: Require raw transport provenance for trusted-header identity.
- `graceful-shutdown`: Require raw transport provenance for loopback-only drain control.

## Impact

Three runtime files, focused regression tests and the two owning specifications/context. No settings, schemas, dependencies or dashboard surface are added. A loopback reverse proxy remains a loopback transport peer; this does not create separate authentication for requests that proxy forwards to internal drain.
