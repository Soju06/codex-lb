## Why

The marking replica can clear an upstream rate-limit reset deadline
merely because a usage sample was recorded after the block. With advisory
foreground usage inference disabled, a fresh but still exhausted window can
therefore reactivate the account and send a sticky session back to it.

## What Changes

- Require available quota in every applicable known window before fresh usage
  can clear an unexpired upstream rate-limit block through early recovery.
- Preserve the existing expiry, credit override, and active-account routing
  contracts; do not turn advisory usage into new account blocks.
- Cover primary and long-window exhaustion plus repeated sticky HTTP requests.

## Impact

- Account-state recovery and regression tests only.
- No database migrations, settings, credentials, or wire-protocol changes.
