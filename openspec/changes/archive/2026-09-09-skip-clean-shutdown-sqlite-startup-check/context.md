# SQLite startup-check context

## Purpose

The SQLite integrity check reads the whole database before the listener binds.
On a multi-gigabyte store this makes an ordinary restart look like an outage.
The optimization in this change is deliberately narrow: skip that scan only
after this process has proved that the same database completed an orderly,
fully drained shutdown.

## Constraints and decisions

- The existing `quick`, `full`, and `off` check modes keep their meanings.
- A missing, corrupt, stale, replaced, or otherwise unproven marker is
  treated as unknown and takes the configured check path.
- The persistent SQLite lock, identity fence, durable sidecar transitions,
  and final shutdown-drain results are all part of the proof. No new setting,
  migration, dependency, or non-SQLite behavior is introduced.
- The clean marker is written only while the process still owns the lifetime
  lock and only after database disposal and every database-owning drain have
  completed. Any timeout, cancellation, or durability uncertainty leaves the
  next startup on the scan path.

## Failure modes

Run-state reads fail open to `unknown`, while writes fail closed: temporary or
target sidecars are removed and the directory entry is synchronized. If that
invalidation cannot be confirmed, startup performs the configured check (when
enabled) and aborts before migrations or serving. A process crash releases the
SQLite transaction but leaves the persistent sentinel file in place; the next
process can then acquire the lock and scan.

## Example

For a 3.7 GB store, a clean shutdown records a matching identity and a
`clean` sidecar. The next startup acquires the lock, records `running`, and
revalidates the identity before skipping the scan. If the store was restored,
the marker was left `running`, or any drain was abandoned, the identity or
completion proof fails and startup logs and runs the configured integrity
check instead.
