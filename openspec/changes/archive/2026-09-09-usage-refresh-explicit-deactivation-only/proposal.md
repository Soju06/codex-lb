## Why

An upstream ChatGPT outage returned bare HTTP 404 responses from the usage endpoint, causing background refresh to deactivate every active account and preventing automatic recovery after the outage ended. Usage refresh must reserve persistent account removal for explicit, account-specific terminal signals rather than infer it from an ambiguous HTTP status.

## What Changes

- Require usage refresh to preserve account status for bare HTTP errors, including 402 and 404, while retaining normal refresh-failure logging, metrics, and retry behavior.
- Continue mapping recognized permanent-failure codes through the existing permanent-failure account-status policy.
- Continue deactivating accounts when the upstream error message explicitly says the account is deactivated.
- Add regression coverage for ambiguous statuses and both explicit terminal-signal paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-refresh-policy`: Restrict usage-refresh account deactivation and reauthentication transitions to explicit permanent-failure codes or explicit deactivation messages.

## Impact

The change affects the background usage updater and its unit tests. It changes no API, database schema, proxy request-path behavior, dashboard surface, settings, or automatic recovery policy for accounts that are already deactivated.
