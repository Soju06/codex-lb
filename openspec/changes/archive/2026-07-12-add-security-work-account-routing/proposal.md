# Add security-work account routing

## Why

Some upstream Responses requests are rejected with a cybersecurity authorization error unless they run on an account enrolled in Trusted Access for Cyber. Today codex-lb treats that response like any other upstream failure, so a pool with mixed account capabilities can fail a request even when an authorized account is available.

## What Changes

- Add a per-account `security_work_authorized` flag that operators can update from the accounts API and dashboard.
- Detect upstream security-work authorization errors on compact, stream, HTTP bridge, and websocket Responses paths.
- Retry eligible unpinned requests on accounts marked as security-work-authorized and emit a non-terminal `codex_lb.warning` before retrying.
- If no authorized account is available, emit a warning and continue normal account selection or preserve the original authorization error, depending on whether retrying is still safe.

## Impact

Cybersecurity-flagged work can use the correct account pool automatically. Normal routing remains unchanged, and pinned file/previous-response requests are not moved to a different account when doing so would break upstream continuity.
