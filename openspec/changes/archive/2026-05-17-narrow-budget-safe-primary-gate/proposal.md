## Why

The budget-safe Responses routing gate currently treats primary and secondary
usage windows as equivalent pressure signals. That can move traffic away from
an account whose short-window budget is still healthy only because its weekly
window is high, even though the short window is the one most likely to cause an
immediate upstream `usage_limit_reached` failure.

When every candidate is already above the primary threshold, the existing
`usage_weighted` fallback can also prefer a nearly exhausted primary-window
account if its secondary usage is lower. That spends the riskiest account first
in the degraded path.

## What Changes

- narrow the budget-safe hard gate to primary-window usage only
- keep secondary-window usage available to routing strategies as a prioritizing
  signal instead of a hard exclusion signal
- make the degraded all-pressured `usage_weighted` fallback choose the least
  primary-pressured account before secondary usage
- add regression coverage for secondary-only pressure and all-primary-pressured
  fallback selection

## Impact

Accounts with high weekly usage but healthy short-window usage remain eligible
for Responses routing. When no healthy-primary candidate exists, the fallback
uses the account least likely to fail immediately on the short window.
