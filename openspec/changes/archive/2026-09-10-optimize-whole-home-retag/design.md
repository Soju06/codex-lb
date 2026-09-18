## Context

See proposal.md. Current main scans transcript JSON repeatedly and repeats SQLite source counts. The existing tests also cover multiple leading legacy provider records, WAL backups and copy-based writes.

## Goals / Non-Goals

Keep the existing public retag command and its session scope. No targeted preview/repair commands, live-client support, automatic rollback or transaction across files and databases.

## Decisions

Cache counts per discovered file/database. Use one grouped SQLite query to derive source matches and aggregate counts. After writes, replace only target contributions with verified counts. Preserve the current SQLite backup and replacement helpers.

Bound JSONL metadata at 64 KiB. A canonical session_meta header ends discovery immediately. Legacy leading records continue until a non-provider record. Reject invalid or oversized initial metadata. After recognized legacy metadata, an incomplete next record at the byte bound belongs to the opaque tail; complete leading legacy metadata records remain supported. Copy the transcript tail without decoding. Metadata parsing belongs in a small separate module; orchestration stays in the retag module.

Use an opt-in progress callback and CLI flag, keeping existing human output. Emit per-item counts and phase starts. Back up through hard links with copy fallback; replacement leaves the backup inode unchanged.

## Risks / Trade-offs

Retag requires stopped clients. Hard links are not independent snapshots if another process writes in place, so that precondition stays explicit. Nonstandard files without supported leading metadata fail before mutation. Verification is scoped to the plan, not a second discovery of a changing home. Failure preserves backups and reports partial work; it does not roll back automatically.

## Verification

Use the accepted public CLI and temporary home artifacts, plus filesystem byte-read and real SQLite trace observation for the explicit performance contract. Both application database environment variables point at one worker-owned disposable file before imports. No live home, account, Docker or routing use.
