## Why

A streaming Responses request with nonportable retained input can receive an
HTTP 429 before any response event is visible. The retry classifier correctly
chooses account failover, but the dispatch wrapper currently records the
rejected account as the payload owner first. Selection then excludes and
requires the same account, so the request surfaces the 429 even when another
compatible account has capacity.

This is distinct from the soft prompt-cache affinity theory discussed in
#1924, #1964, and #1965. Prompt-cache exclusion already selects a replacement
on current `main`; the conflict comes from the newly established dispatch-owner
requirement.

## What Changes

- Treat a pre-visible HTTP 429 as a definitive rejection that does not create a
  new dispatch-owner binding for that attempt.
- Preserve independently established file, continuation, turn-state, and
  other hard account owners.
- Add a routed regression using compacted input and prompt-cache affinity so
  the test exercises real account selection and failover.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define payload ownership after a pre-visible HTTP 429
  rejection.

## Impact

The change is limited to streaming Responses retry ownership after HTTP 429.
It adds no settings, schema, migration, dashboard, or frontend changes.
