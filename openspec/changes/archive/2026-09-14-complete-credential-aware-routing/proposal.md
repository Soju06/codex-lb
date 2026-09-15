## Why

PR #2132 review found remaining consumers that disagree with credential-aware routing, a persistence-conflict fallback that weakens proven rejection, and cache-wide repair fencing that suppresses unrelated account marks.

## What Changes

- Apply reason-and-expiry eligibility to usage, reset-credit, and dashboard-capacity consumers while retaining independent refresh-token rules.
- Preserve proven access rejection during refresh persistence conflicts.
- Scope local repair fencing to the account being repaired.

## Capabilities

### Modified Capabilities
- `account-routing`: Consistent consumer eligibility and rejection preservation.

## Impact

Existing account consumers, auth fallback, routing cache, and regression tests. No schema, dependencies, or configuration changes.
