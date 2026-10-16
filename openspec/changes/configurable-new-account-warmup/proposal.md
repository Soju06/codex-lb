## Why

Automatically enabling warm-up for new accounts needs an operator-controlled default. A dashboard setting lets operators choose enrollment behavior without changing each account or editing code.

## What Changes

- Add a persisted `limitWarmupAutoEnableNewAccounts` boolean and a switch in Settings → Routing → Warm-up.
- Default to disabled, preserving existing installations; the global traffic switch remains independent and disabled by default.
- Read this default when OAuth or auth.json import creates an account, preserving existing preferences on reauthentication and replacement.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Make new-account warm-up enrollment configurable through the dashboard settings API and UI.

## Impact

Dashboard settings persistence, migration, API and frontend contracts, OAuth/import services, and regression coverage. No new environment variable, navigation item, immediate probe, or existing-account rewrite.
