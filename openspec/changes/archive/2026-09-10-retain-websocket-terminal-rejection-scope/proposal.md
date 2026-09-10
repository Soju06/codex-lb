## Why

WebSocket terminal cleanup and handshake rejection drop the known request model and service-tier scope when writing account health. This violates the accepted rejected-scope requirement in #2327 and prevents a matching probe from recovering such a hold.

## What Changes

- Preserve the scope of the pending request selected for the finalizer's health penalty.
- Carry the request scope through handshake error classification so a completed matching probe can recover the hold.
- Extend public WebSocket EOF and pending-request finalization regressions without changing settlement ordering.

## Capabilities

### New Capabilities

### Modified Capabilities

- `account-routing`: cover selected request scope during shared WebSocket finalization.

## Impact

The finalizer and handshake classifier calls, regression tests and the existing routing spec. No changes to penalty selection, ownership, reservation settlement or operator probe admission.
