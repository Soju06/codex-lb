## Why

Forwarded compact requests can exhaust their request budget after reserving API-key quota but before calling upstream. Several compact preflight exits raise the existing timeout error without settling that reservation, so later requests can be rejected by phantom usage until stale-reservation cleanup runs.

## What Changes

- Settle any held API-key usage reservation before each compact preflight budget-exhaustion error that otherwise escapes without cleanup.
- Preserve the existing account-lease release and `502 upstream_request_timeout` response contract.
- Keep inner upstream-call budget exits unchanged because their enclosing handler already settles the reservation.
- Add route-level regression coverage for cleanup and exactly-once settlement.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `api-keys`: Extend the compact reservation-cleanup invariant to forwarded preflight budget-exhaustion terminals.

## Impact

- Affected code: `app/modules/proxy/_service/compact.py`
- Affected tests: `tests/integration/test_proxy_compact.py`
- External API behavior: no status, error-code, or payload change
- Persistence impact: abandoned API-key reservations are released immediately instead of remaining charged until stale cleanup
