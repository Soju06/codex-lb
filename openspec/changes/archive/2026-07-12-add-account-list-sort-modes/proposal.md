# Add account list sort modes

## Why

Operators need to inspect accounts by reset timing or account name without
losing the existing reset-soonest default. The dashboard-visible sort behavior
needs a stable contract so future account-list changes do not reorder stale or
unknown reset rows ahead of accounts with real upcoming resets.

## What Changes

- Document the Accounts page sort selector modes for reset time and account
  name.
- Keep reset-soonest as the default ordering.
- Require unknown or elapsed reset timestamps to sort after finite upcoming
  reset timestamps in both reset-time modes.

## Issue Trace

- Closes #851

## Impact

- **Spec**: `frontend-architecture`
- **Behavior**: account-list ordering and selected-account fallback ordering
