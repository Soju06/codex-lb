# Configure account concurrency caps

## Why

Operators currently have to restart the proxy to change per-account response-create and stream concurrency caps. A live overload can therefore only be corrected through environment changes and restart, even though the dashboard already owns other routing controls.

## What Changes

- Persist the response-create cap, stream cap, and stream recovery reserve in dashboard settings.
- Expose validated nonnegative values through `GET`/`PUT /api/settings`.
- Make new account selection, lease acquisition, admission, and cap-error reporting consume the dashboard settings cache rather than mutating process configuration.
- Seed a newly-created settings row from the current environment limits. Existing rows retain nullable overrides so they continue inheriting their effective environment limits until an operator saves dashboard values.

## Non-goals

- Per-account overrides or changing existing in-flight leases.
- Replacing the existing recovery-reserve semantics; it remains a subtractive selection reserve for ordinary streams.
