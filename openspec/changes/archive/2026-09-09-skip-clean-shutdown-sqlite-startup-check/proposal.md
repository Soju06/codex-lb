## Why

`init_db()` runs `PRAGMA quick_check` (or `integrity_check`) over the whole
SQLite file before anything else, and the listener does not bind until it
returns. The scan reads every page, so its cost grows with the store while
the operator sees nothing at all: no log line marks the start, so a restart
looks like a hang.

On a 3.7 GB store this is 177 seconds of connection-refused on every
restart, measured on a running deployment across two consecutive restarts
(178 s and 181 s). The stall sits entirely ahead of Alembic, which then
reports `Database schema already at head; skipping upgrade`, so migrations
are not the cost and the check is paid in full even when nothing changed.

SQLite is already consistent after a clean close. The scan defends against
filesystem and hardware corruption, which does not correlate with an
operator restart, so paying for it on every start buys nothing in the common
case and turns every deploy into a multi-minute outage.

## What Changes

- Record how each process left the SQLite store in a `<db>.runstate`
  sidecar: `running` once startup has begun, `clean` after the engines are
  disposed during an orderly shutdown.
- Acquire a persistent `<db>.runstate.lock` SQLite sentinel before reading the
  sidecar, hold it for the process lifetime, and fail startup closed when
  another process already owns it. Release it only after the clean transition;
  SQLite releases it automatically if the process dies.
- Skip the startup integrity scan only when the sidecar records a clean
  shutdown. A crash, an OOM kill, a power loss, a first run, and an upgrade
  from a build that never wrote a sidecar all still run the scan.
- Read the prior record and persist `running` before making that skip
  decision. If the running transition cannot be recorded, startup takes the
  scan path and a failed startup leaves `running` where the sidecar can be
  written. If cleanup of an untrusted prior marker, or the directory sync
  that proves that cleanup, cannot be confirmed, startup runs the configured
  check when enabled and then aborts before migrations or serving rather than
  continuing with an unproven marker.
- Use an exclusive randomized temporary file for each sidecar write so a
  local symlink cannot redirect the write before the atomic replacement.
- Announce the scan before it starts (path, mode, file size) and log its
  duration when it passes, so the stall is attributable when it does happen.

Failure modes resolve toward checking. A sidecar that is missing,
unwritable, undecodable, or unrecognized reads back as unknown, and a failed
write removes the file rather than leaving a stale `clean` behind. A `clean`
record carries the database file's device, inode, size, mtime, and ctime and
is discarded once any of them changes, so even a timestamp-preserving restore
cannot inherit the previous file's clean record. Both the record and its
directory entry are fsynced, so a power loss cannot keep an earlier `clean`
while losing the `running` transition. `clean` is recorded only after the
engines actually finished disposing.
The clean identity, the newly persisted running identity, and the current
database identity must all be present and equal at the final skip decision;
replacement in any window forces the scan. If the directory fsync fails after
the replacement, cleanup removes the sidecar and fsyncs the directory again.
The lifetime lock makes that ownership rule process-wide: startup cannot trust
another process's `clean` marker, and a clean marker cannot be written after
the lock has been released. SQLite releases the transaction when the owning
process dies, so a subsequent process can recover ownership without deleting
or trusting the persistent sentinel file.

`clean` is withheld when any bounded database-owning shutdown drain is
unfinished: reclaimed SQLite teardown, HTTP bridge durable-session marking or
closure, final proxy persistence, detached audit/fleet control-plane work, or
scheduler leader-release. A timeout or failed drain therefore deliberately
makes the next startup pay the scan.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-backends`: when the SQLite startup integrity check runs, and the
  observability it emits.

## Impact

No new setting, no new dependency, no schema change, and no change for
non-SQLite backends. The existing
`CODEX_LB_DATABASE_SQLITE_STARTUP_CHECK_MODE` (`quick` / `full` / `off`)
keeps its current meaning and default; this only removes redundant runs of
the mode already selected. The added artifact is one small sidecar file next
to the database plus one persistent SQLite lock sentinel next to it.

Operators who want the scan on every start regardless can still get it: the
sidecar only ever suppresses a scan after this process itself recorded a
clean close.
