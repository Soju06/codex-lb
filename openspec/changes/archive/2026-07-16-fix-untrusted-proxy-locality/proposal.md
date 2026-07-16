## Why

Trusted-proxy mode currently treats the mere presence of a forwarded client-IP header as provenance for loopback locality, even when the socket peer is outside every configured trusted-proxy CIDR. A remote request relayed over an untrusted loopback proxy can therefore be classified as local and bypass dashboard bootstrap or disabled-API-key remote protections.

## What Changes

- Require the loopback socket peer to match a configured trusted-proxy CIDR before a forwarded client-IP header can establish local request identity.
- Keep direct-local behavior unchanged when proxy-header trust is disabled and no non-empty forwarded client-IP hint is present.
- Inspect every repeated forwarded client-IP field in direct mode so a later non-empty value cannot be hidden behind an empty first field.
- Keep valid forwarded loopback identity from a configured trusted proxy classified as local.
- Add request-locality and dashboard-bootstrap regressions for untrusted loopback proxy sources.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `admin-auth`: Make dashboard locality depend on forwarded identity accepted from a configured trusted proxy, not raw header presence.
- `api-keys`: Keep unauthenticated protected proxy access fail-closed when forwarded headers arrive from an untrusted socket peer.

## Impact

- Affected code: `app/core/request_locality.py` and its dashboard/proxy-auth callers through the shared locality predicate.
- Affected tests: request-locality unit coverage and dashboard authentication integration coverage.
- Configuration and wire formats remain unchanged; deployments whose actual proxy socket address is missing from `firewall_trusted_proxy_cidrs` remain remote until configured correctly.