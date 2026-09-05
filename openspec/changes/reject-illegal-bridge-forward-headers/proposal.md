## Why

Owner-forwarded HTTP bridge requests reconstruct WebSocket metadata into HTTP
headers before posting to the target owner. If reconstructed bridge metadata
contains illegal HTTP control characters, aiohttp can reject serialization
outside the proxy's structured error path, and reservation metadata can be
silently lost on the wire while the origin still treats the owner as settlement
authority.

## What Changes

- Reject illegal control characters in signed bridge-forward context metadata
  before building signatures or posting to an owner.
- Drop unsafe ordinary client headers rather than forwarding them to aiohttp.
- Preserve reservation ownership by failing closed when reservation metadata is
  unsafe instead of omitting only the signed reservation headers.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: owner forwarding fails closed on illegal
  reconstructed HTTP header metadata.
