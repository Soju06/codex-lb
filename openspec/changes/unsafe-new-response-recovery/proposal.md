## Why

Some upstream Responses sessions reject a stale `previous_response_id` even
when the client still has the complete conversation. The existing fail-closed
behavior cannot continue those sessions without a client restart.

## What Changes

- Add explicitly disabled operator flags for complete-transcript recovery and
  unsafe fresh-response recovery.
- When both flags are enabled, replay only a verified full-history request
  without the stale anchor and bind the upstream-minted new response ID to the
  existing downstream session.
- Recognize the upstream's terse `Invalid previous_response_id` error only in
  this opt-in path.
- Keep delta-only requests, incomplete history, ambiguous matches, and repeats
  fail-closed.

## Impact

This is an at-least-once recovery path. The original upstream turn may have
committed before its anchor became unavailable, so replay can duplicate model
output or tool side effects. The flag remains false by default.
