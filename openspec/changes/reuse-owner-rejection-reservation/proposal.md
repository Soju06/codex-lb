## Why

When an HTTP bridge owner rejects a bootstrap request before upstream
dispatch, the origin can recover locally. The recovery must transfer the
existing API-key usage reservation instead of acquiring a second hold.

## What Changes

- Reuse the original reservation during pre-dispatch local recovery.
- Preserve the existing owner-forward classification and fail-closed paths.
- Add regression coverage for exactly-once reservation ownership.

## Capabilities

### Modified Capabilities

- `sticky-session-operations`: local owner-forward recovery preserves the
  original API-key reservation.
