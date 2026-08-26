# Context

## Purpose

Issue #1538 needs a reversible dashboard override. Operators must be able to
experiment with a per-account capacity value and later return to the process
environment without knowing or re-entering that environment value.

## Decision

The API will expose effective values and raw nullable overrides separately.
Existing effective fields remain stable for current clients. New override
fields make the persistence state visible to the dashboard and prevent the UI
from confusing an inherited value with a pinned value.

The update contract follows the already shipped retention-settings pattern:

- field absent: leave the stored override unchanged;
- field present with `null`: store `NULL` and inherit the environment value;
- field present with a number: store that number as a dashboard override.

## Constraints

- The database columns are already nullable and the runtime already resolves
  `NULL` through the environment-backed settings cache.
- Validation of stream recovery reserve must use the effective candidate
  values, including when one of the submitted fields clears its override.
- Full settings saves must not convert an inherited value into a pinned value
  merely by echoing the effective response field.
- Existing optimistic version checks, cache invalidation, and audit tracing
  remain authoritative.

## Failure Modes

- An omitted field must never clear an existing override.
- A clear request must not be interpreted as a no-op.
- An invalid numeric value must leave all existing overrides unchanged.
- A stale `expectedVersion` must still return the existing settings conflict.
- Clearing one capacity field must not pin or modify the other three fields.

## Example

With `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT=8` and a stored dashboard override
of `16`:

```json
{
  "proxyAccountStreamLimit": null
}
```

must persist `NULL`, return an effective `proxyAccountStreamLimit` of `8`,
and report `proxyAccountStreamLimitOverride` as `null`. A later environment
change to `12` must then be reflected without another dashboard update.
