## Why

An upstream `deactivated_workspace` rejection currently leaves the selected workspace account active and classifies the request as non-retryable. A pooled, account-neutral request therefore surfaces HTTP 402 instead of trying a healthy candidate, and later requests can select the same unavailable workspace again.

## What Changes

- Recognize the exact structured `deactivated_workspace` code as a terminal failure of the selected workspace routing record.
- Classify that rejection as account-unavailable so deterministic pre-visible failover can select another eligible account without retrying the rejected workspace.
- Preserve downstream visibility, hard account ownership, reservation settlement, and bare-status safety guards.
- Add regression coverage through the public Responses and compact routes, plus classifier and account-state tests.

## Impact

- Affected capabilities: `account-routing`, `usage-refresh-policy`.
- Reuses existing permanent-failure persistence and deterministic failover; no settings, schema changes, dependencies, or setup steps.
- Other workspace/account records belonging to the same identity remain unchanged. Bare HTTP 402/404 errors are not terminal evidence.
