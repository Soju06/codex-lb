## Why

When an API key has existing request-log usage and an admin later adds a limit rule, the new limit starts at `0`. This makes the dashboard and enforcement state disagree with actual usage in the current limit window.

Issue #518 reports the visible case: a key has already used about 10k tokens, then a 100k daily token limit is added, but the limit shows `0/100k` instead of `10k/100k`.

## What Changes

- Backfill newly-added API key limit rules from existing request logs in the active window.
- Preserve current values for existing matching limits unless the admin explicitly resets usage.
- Keep `resetUsage=true` as the explicit way to start all submitted limits from zero.

## Impact

- Admins can add limits to an already-used API key without losing current-window usage visibility.
- Enforcement immediately accounts for current-window usage on newly-added rules.
- No schema, API shape, or frontend contract changes are required.
