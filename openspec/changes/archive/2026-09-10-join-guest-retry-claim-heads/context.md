# Join guest sessions with retry-claim history

Candidate feaa8db312d7e540b306056ad9c981491dd26701 and main d6a7ca662860e2b427e462f434319273bcd240bd compose to heads 20260910_160000_merge_retry_claim_spool_heads and 20260908_000000_add_guest_session_generation. The latter's published parent is 20260910_010000_dashboard_spool_retention. Keep those edges intact.

A database on the receipt/spool join gains guest_session_generation with its incoming zero default and retains its active receipt and spool value. A guest-parent database retains a nonzero generation and spool value while adding nullable receipt fields. Joining or downgrading only the join changes version stamps without changing either schema or its rows. Removing receipt schema remains governed by its existing live-receipt guard.

The original graph-repair evidence stays immutable. Receipt reclaim/reset/settlement and mixed-version decisions stay open. No broader guest authorization changes or other PR graph changes belong here.
