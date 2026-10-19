# Route targeted reauthentication by account

## Why

Targeted reauthentication already identifies the existing local account, but OAuth route resolution ignores that identity. A bound account fails without a default proxy pool, or exchanges its token through a different default pool. An intentionally direct account is also blocked by another account's binding.

## What Changes

- Use the selected local account's current route for browser callbacks, manual callbacks, device-code initialization, and device-token polling.
- Preserve the default-pool guard for OAuth flows without an intended account.
- Reject nonexistent targeted accounts before upstream I/O, and preserve existing seat/workspace validation before credential replacement.

## Impact

This refines the outbound HTTP OAuth routing policy without adding settings, public request fields, or database changes. Browser network configuration remains external to the server. New-account proxy enrollment and preserving a route snapshot across configuration changes are outside this change.
