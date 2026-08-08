## Context

SQLite compiles SQLAlchemy `with_for_update()` without a row-lock clause. The
old implementation therefore read `owner_epoch` outside `sqlite_writer_section`
and later overwrote the row unconditionally. Two in-process claims could mint
one fencing token, allowing a stale release to close the current session and
leaving `account_id` paired with another claim's response anchor.

## Decision

Run the existing-row read, takeover decision, compare-and-set `UPDATE`, alias
cleanup, and commit inside the same writer section. The update predicate must
match the row id plus the observed `owner_instance_id` and `owner_epoch`.
PostgreSQL continues to use `FOR UPDATE`; a zero-row CAS result retries.
