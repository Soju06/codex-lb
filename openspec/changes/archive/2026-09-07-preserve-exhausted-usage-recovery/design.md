## Context

See [proposal.md](proposal.md). A debugger trace of the existing HTTP refresh/cache regression showed `_state_from_account` clearing a future runtime reset on a post-block sample that still reported 100% primary usage. `apply_usage_quota` then reopened the account because foreground selection intentionally disables inference of new account blocks from advisory usage.

## Goals / Non-Goals

The existing early-recovery branch should distinguish new evidence from evidence of available quota. Account selection, status persistence, and HTTP error mapping continue through their current production paths. This does not change ranking, ACTIVE-account eligibility, persisted cooldown lengths, or response assertions.

## Decisions

Keep the fix at the reset-clearing condition. Use the applicable window's normalized usage so an elapsed sample can still support recovery. Keep the existing freshness-window selection, block timestamp comparison, and observing-replica cooldown gate. Do not enable general quota inference in foreground selection: that could promote advisory reset values into new blocks.

Extend the existing deterministic fresh-primary recovery test with exhausted and elapsed-window cases. The existing HTTP regression remains the proof that refresh persistence and cache invalidation lead to the structured response before another upstream request.

## Risks / Trade-offs

- Reading a raw sample's percentage could block recovery after that sample's window expired; use the existing normalized value.
- Treating every 100% sample as a routing block would violate ACTIVE-account advisory usage; constrain the condition to existing blocked-account recovery.
- Credit-backed secondary usage, zero-primary plans, and peer cooldown/CAS behavior retain their existing implementation and coverage.
