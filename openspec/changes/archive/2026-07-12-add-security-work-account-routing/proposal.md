# Add security-work account routing

## Why

Some upstream Responses requests are rejected with a cybersecurity authorization error unless they run on an account enrolled in Trusted Access for Cyber. Today codex-lb treats that response like any other upstream failure, so a pool with mixed account capabilities can fail a request even when an authorized account is available.

## What Changes

- Add a per-account `security_work_authorized` flag that operators can update from the accounts API and dashboard.
- Detect upstream security-work authorization errors on compact, stream, HTTP bridge, and websocket Responses paths.
- Retry eligible unpinned requests, plus previous-response requests with a validated self-contained fresh replay, on accounts marked as security-work-authorized and emit a non-terminal `codex_lb.warning` before retrying.
- For a classified root Codex session, persist the requirement and preserve the original authorization error if no authorized account is available; it must not fall back to the ordinary pool. Unrooted requests retain the existing optional normal-failover behavior.

## Impact

Cybersecurity-flagged work can use the correct account pool automatically. Normal routing remains unchanged, and pinned requests move only when codex-lb has a validated fresh body that no longer depends on the unavailable upstream object.
