## Why

Issue #1682: a single-instance SQLite deployment hit a self-sustaining ~17-minute `database is locked` stall that blocked leader re-election. The log evidence shows the lease loss was a symptom — some connection held SQLite's single writer slot past the 30-second busy timeout, starving every other writer, and recovered spontaneously when the holder finally finished. The repro is nondeterministic and the holder's identity never appears in any log, so the stall cannot currently be attributed, and any teardown/deadline fix would be guesswork until it is.

## What Changes

- Every SQLite engine gains a long-write-transaction watchdog: the first write statement in a transaction starts the clock (WAL takes the writer slot at the first write, not at BEGIN), and when the transaction commits or rolls back after holding longer than the busy timeout, a WARNING reports the held duration, outcome, owning task name, and the first and last write statements.
- Post-hoc by design: the stall self-recovers, so identifying the holder when it ends is sufficient for attribution and needs no sampler thread. Read-only transactions never report; fast writes never report.
- No new settings: the threshold is the existing busy timeout — a writer holding longer is precisely the one making every other writer surface `database is locked`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-backends`: SQLite write-lock stalls are attributable from the log.
