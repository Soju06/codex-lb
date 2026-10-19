# Route account diagnostic probes through account policy

## Why

Force Probe and quota-planner warmup send account credentials through generic outbound paths without resolving the selected account's proxy binding. This bypasses the account-bound routing used by normal requests and usage refreshes.

## What Changes

- Resolve Force Probe and quota warmup routes using the selected local account ID.
- Use the existing routed client for bound probes and preserve ordinary direct routing for unbound accounts.
- Fail before dispatch for unavailable bound routes; preserve existing network-failure status behavior.
- Release header-only probe responses and owned clients even when cancelled.
- Record quota warmup route metadata in its existing request logs.

## Impact

This changes diagnostic and warmup routing without adding settings, account-selection rules, database columns, or new API fields. Existing quota claims, usage reservations, and settlement remain in control. Force Probe keeps its existing audit and status surface.
