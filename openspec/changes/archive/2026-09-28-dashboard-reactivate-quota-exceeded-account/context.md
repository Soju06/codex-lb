# Operator quota retry

The upstream usage API can place a weekly-only quota in the primary slot. The dashboard already interprets this as a weekly window, but the usage updater expected a secondary slot when recovering a quota-exceeded status. That mismatch can leave a Professional reserve account blocked while the dashboard shows 97% remaining. The dashboard previously showed Pause for `quota_exceeded` accounts, while the existing reactivation endpoint could already clear that state.

Resume is an explicit operator retry. For example, after three active accounts exhaust, an operator may resume a reserve account that the dashboard still marks quota-exceeded. A subsequent upstream quota rejection will restore the block. Re-authentication remains the recovery path for accounts marked `reauth_required`.
