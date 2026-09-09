## Why

Account usage caps can be applied inconsistently after usage refreshes and across reused trusted-capability WebSockets, while the dashboard can reserve weekly capacity for non-weekly windows. The capped portion of quota bars also relies on gray alone, making it harder to distinguish for colorblind users.

## What Changes

- Refresh account-selection and reused-connection cap state after every successful standard-usage write.
- Let trusted-capability WebSocket rerouting replace a capped reused account before enforcing its cap.
- Apply weekly pace caps only to the standard weekly window.
- Render capped quota-bar capacity with a diagonal hatch pattern in addition to color.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Keep usage-cap eligibility current across refresh paths and preserve trusted-capability rerouting on reused WebSockets.
- `account-quota-presentation`: Apply weekly caps only to weekly data and distinguish capped bar capacity without relying on color alone.

## Impact

Affected areas are proxy usage refresh and WebSocket routing, dashboard weekly pace calculation, quota-bar styling, their regression tests, and the two owning OpenSpec capabilities. No API or database schema changes are introduced.
