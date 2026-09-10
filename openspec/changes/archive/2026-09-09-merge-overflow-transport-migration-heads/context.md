# Overflow and transport graph repair

Pinned main `713fa5648d23a47087381204b6fb841bd36cbe88` contains two heads.
Both descend from `20260830_000000_add_quota_warmup_claim_expiry`. The repair
adds a shared forward merge rather than editing either already-merged parent.

The merge itself has no DDL or DML. An upgrade from just one parent still runs
the other parent's original operations. In particular, the transport parent
normalizes the retired `default` sentinel to `auto`; the merge does not change
that decision. An existing explicit transport choice must survive.

Downgrading the merge to either immediate parent unmerges revision tracking.
Alembic retains both parent stamps and both parent schemas. This is not a
rollback to an image that knows only one branch, nor a promise that downgrading
the original subscription-overflow migration preserves its pins. Re-upgrade
only restores the merge stamp.

Tests observe Alembic revisions, real SQLite schema and populated rows. Hosted
migration policy and PostgreSQL checks supply the second-dialect evidence.
No live database or deployment is part of this work.
