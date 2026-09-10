# Dashboard account history and retry-claim composition

Actual target8e5760726a34332d869aac682a3932170621966b extends guest-session generation through roles000000, users010000, compat-credential020000 and audit030000. The other published head is20260910_170000_merge_guest_retry_claim_heads. The new join targets the complete incoming chain.

A populated retry-parent database retains its live receipt, nondefault spool value and guest generation while receiving the incoming role/user schema and documented credential backfills. An audit-parent database retains custom roles, scoped grants, users, session generations and audit actor snapshots while receiving nullable receipt fields. Existing audit rows retain the incoming migration's SQLite timestamp normalization; new actor/target fields default to null and severity to info.

Downgrading only the join restores both parent stamps without dropping either branch or changing rows. Previous repair/review/hosted records stay immutable. The earlier7325 review applies to9cf only; fe25cf5c is the reviewed8e composition. The200000 revision was introduced locally and adjusted before any publication. Every migration already published on either input remains unchanged.

No sibling PR dependency or auth/receipt policy is introduced. Original receipt and live-activation gates remain open.
