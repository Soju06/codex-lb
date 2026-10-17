## Why

Security-audit rows contain raw actor IPs, request IDs, and arbitrary identifying
details. The route currently uses the general dashboard read gate, allowing a
read-only guest to receive operator-sensitive records.

## What Changes

- Require an admin dashboard principal for `GET /api/audit-logs`.
- Return existing HTTP 403 `admin_access_required` behavior for guests.
- Preserve the raw audit response contract for authorized operators.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `admin-auth`: classify security-audit records as admin-only sensitive data.

## Impact

Audit router authorization and focused API tests only. No persistence,
filtering, schema, frontend, setting, or operator-detail change.
