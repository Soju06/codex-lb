# Design

Join 20260910_010000_dashboard_spool_retention and 20260910_010000_merge_desktop_reset_pool_heads without editing either parent. The merge performs no schema operations. Upgrading an existing reset database applies main's nullable retention field; upgrading an existing main database applies the reset schema with its existing default-off policy. A downgrade of only this merge restores the two parent stamps without removing either schema or data.
