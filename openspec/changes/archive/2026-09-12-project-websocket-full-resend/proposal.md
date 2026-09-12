## Why

A direct WebSocket follow-up can retain the complete transcript but fail quota recovery because historical response IDs and encrypted reasoning bind the saved resend to the exhausted account.

## What changes

Reuse the existing account-neutral input projection for a proxy-anchored full resend after prefix verification and a retained prior assistant reply. Keep the normal anchored send intact and use the projected body for the existing safe-replay path.

## Impact

Direct WebSocket replay preparation and regression tests. No settings, schema, or HTTP routing changes. This addresses part of #1707; selection-time owner loss remains outside this change.
