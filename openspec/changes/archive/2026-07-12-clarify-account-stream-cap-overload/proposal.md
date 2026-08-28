## Why

Account-local stream cap exhaustion already returns the stable `account_stream_cap` code, but the selection message can still be formatted as `No available accounts` and then expanded into a degraded-upstream message. Operators then see "all upstream accounts are unavailable" even though the real action is to wait for streams to finish or raise the per-account stream cap.

## What Changes

- Return an account-cap-specific message when every otherwise eligible account is filtered by a local account cap.
- Preserve the stable `account_stream_cap` / `account_response_create_cap` reason codes.
- Keep true empty-pool failures on the existing no-account/degraded path.

## Impact

- Stream-cap failures become local-overload diagnostics instead of ambiguous upstream-unavailability messages.
- No routing limits or default cap values change.
