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
- Skip the startup integrity scan only when the sidecar records a clean
  shutdown. A crash, an OOM kill, a power loss, a first run, and an upgrade
  from a build that never wrote a sidecar all still run the scan.
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
to the database.

Operators who want the scan on every start regardless can still get it: the
sidecar only ever suppresses a scan after this process itself recorded a
clean close.
