## Design

The request retains shielded cleanup ownership through the existing settlement and fail-safe release attempts. Both already use bounded SQLite contention retry in ApiKeysService. If neither confirms cleanup, the committed reservation remains accounted for and is eligible for the existing stale-reservation scheduler. Do not allocate a detached retry or a queue entry. Preserve `usage_settlement_failed`, `reservation_released=False`, cleanup readiness and suppressed account-health writes.

The hourly scheduler uses the existing six-hour idle cutoff and 24-hour hard ceiling. Failed persistence may therefore hold quota for hours; this is exceptional recovery, not successful release. No timing guarantee is added. The process persistence observer describes registered work only and cannot certify the absence of unresolved durable reservations.

Removing the new compact retry also removes the unclassified work rather than extending the stream classifier. Existing stream settlement/callback ownership remains unchanged.
