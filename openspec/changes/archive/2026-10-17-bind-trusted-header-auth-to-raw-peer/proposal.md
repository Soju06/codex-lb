## Why

Trusted-header dashboard authentication currently decides whether to trust the
configured identity header from the proxy-projected client address. A direct
client can therefore supply forwarding headers that make an untrusted socket
peer appear to belong to a trusted proxy network before the authentication
middleware evaluates the request.

## What Changes

- Bind trusted-header source authorization to the launcher-preserved raw socket
  peer rather than the projected client identity.
- Scrub the configured identity header when the raw peer is missing, untrusted,
  or proxy-header trust is disabled.
- Add full-request regression coverage for both trusted proxy forwarding and an
  untrusted peer attempting to project itself into a trusted proxy CIDR.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: Require trusted-header principal attribution to authorize the
  raw socket peer before accepting the configured identity header.

## Impact

The change is limited to dashboard trusted-header authentication middleware,
trusted-header request authentication, and their integration tests. It adds no
setting, dependency, API shape, migration, or dashboard-visible change.
