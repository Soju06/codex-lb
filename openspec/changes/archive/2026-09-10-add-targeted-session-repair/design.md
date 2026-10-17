## Context

Current retag is a whole-home migration. Its CLI and backup helpers already establish the supported tags, explicit home selection and SQLite backup mechanism. See proposal.md for the accepted feature and partial issue scope.

## Goals / Non-Goals

Provide a targeted CLI repair with bounded header discovery and exact recovery evidence. Whole-home retag optimization, provider selection and automatic recovery are outside this independent slice.

## Decisions

Use the CLI as the regression seam already required by #1636. Keep discovery and mutation behind a small local session-metadata interface. Read only the first JSONL metadata line, capped at 1 MiB, and preserve the remaining bytes without parsing. Support current session_meta payloads and the existing legacy top-level metadata shape. Match SQLite IDs exactly, without interpolating IDs into SQL.

Build a fresh plan for every repair instead of trusting saved previews. Reject unknown selected IDs and unsupported tags. Validate file identity before backup and replacement, compare SQLite provider values when updating, and verify selected records afterward. Codex must be closed, as with existing retag. These checks detect stale plans but do not provide a transaction across independent files and databases.

Reuse existing SQLite backup support. Prefer hard links for JSONL snapshots and atomically replace originals so backup inodes retain old bytes. Copy when hard links are unavailable. Retain backups on failure and identify the recovery path in the error.

## Risks / Trade-offs

Concurrent writers can race multi-file repair. Require a stopped client, check planned inputs again and retain complete backups. Automatic rollback could overwrite later work and is excluded.

Unsupported or malformed metadata cannot safely be rewritten. Reject unknown selected IDs, unsupported tags and oversized metadata rather than infer identity from filenames. A session represented in only one storage format is not a mismatch.
