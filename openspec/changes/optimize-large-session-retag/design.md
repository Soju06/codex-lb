## Context

The current retag implementation enumerates the same JSONL set repeatedly, parses every record to build provider counts, scans again to select targets, copies every target, rewrites every target, and parses the full home again after mutation. ProviderSwitcher supervises that work with a fixed 300-second process timeout and only reads the completed output. A 9.5 GiB Codex home therefore reached the timeout after creating its backup and partially rewriting files; the supervisor discarded the already-emitted backup path and could not prove rollback.

The CLI already requires Codex process quiescence for writes. That invariant makes same-volume hard-link backups safe when each target is replaced with a newly written file rather than modified in place.

## Goals / Non-Goals

**Goals:**

- Make discovery proportional to the number of sessions rather than total transcript content by reading only each file's first provider-bearing session metadata record.
- Preserve byte-exact rollback evidence before any target is replaced.
- Read and rewrite each selected JSONL once, leaving unrelated bytes unchanged.
- Eliminate duplicate SQLite planning queries and full-home post-scans.
- Emit structured progress frequently enough for ProviderSwitcher to distinguish long work from a stall.
- Let ProviderSwitcher stop a process only after progress has been idle, while retaining all output and backup identities for rollback.

**Non-Goals:**

- Running a real provider switch or repairing the user's current session metadata during implementation validation.
- Changing supported provider names or command-line confirmation semantics.
- Using hard links for live SQLite databases, which require SQLite-aware backups.

## Decisions

### One immutable execution plan

Discovery creates a typed plan containing every JSONL path, its single session provider, selected JSONL targets, each SQLite database's grouped provider counts, selected SQLite targets, and aggregate before counts. JSONL discovery reads only the bounded first JSONL record, accepting an optional UTF-8 BOM, and treats a missing, oversized, or malformed first metadata record as unavailable instead of parsing transcript records. Each SQLite database is queried once with a grouped provider count query.

The plan is reused for backup, mutation, result counts, and targeted verification. Provider counts after mutation are derived by moving the planned match counts from source to target and are accepted only after targeted verification succeeds.

### Hard-link JSONL backups with copy fallback

For each selected JSONL, the backup destination is created before mutation. The implementation first calls the platform hard-link operation. Because the backup directory is under the same Codex home and write mode requires quiescence, the link points at an immutable original inode. The live path is then replaced with a separately written temporary file, so the backup continues to reference the original bytes.

If link creation is unsupported, crosses a volume, or is denied by the filesystem, the implementation copies the original to a new backup file, flushes the copy, preserves metadata, and verifies its size before allowing replacement. Unexpected backup errors abort the operation before that target is replaced. SQLite databases continue to use SQLite's backup API.

### Single-pass atomic JSONL rewrite

A selected JSONL is opened once in binary mode and streamed to a temporary sibling. Only the planned provider-bearing metadata record is decoded and normalized; an existing UTF-8 BOM is preserved, and all other lines are copied byte-for-byte. The temporary file is flushed and atomically replaces the live path. A failed write removes the temporary file and leaves the original path intact. The SQLite copy fallback likewise consolidates and flushes its sibling temporary database before atomically replacing the live database rather than copying over the live path.

### Targeted verification

After mutation, verification reads only the provider-bearing metadata record from JSONL files actually replaced and queries only SQLite databases selected by the plan. It asserts that no planned source-provider metadata remains and that the expected number of rows moved. It does not rescan unrelated JSONL transcript content, unrelated files, or unselected databases.

### Structured progress protocol

The CLI emits newline-delimited events prefixed with `CODEX_LB_RETAG_PROGRESS `. The JSON payload contains `phase`, `completed`, `total`, `unit`, and `message`. Phases cover discovery, backup, JSONL rewrite, SQLite update, and verification. Copy fallback and long rewrites emit chunk-level progress; other loops emit periodic item-level progress. Human-readable summary output remains compatible.

ProviderSwitcher parses these events into a typed 64-bit progress record, updates a phase label and progress bar, and resets an idle timer only when completed work increases or the operation makes a valid phase/total transition. Repeated identical events do not mask a stall. There is no total-duration deadline. If progress does not advance for the configured idle interval, the process tree is terminated, accumulated stdout/stderr is retained, both the immediate `Created backup at ...` identity and final `- Backup: ...` identity are accepted only after path validation, and rollback is attempted. A timeout without valid backup evidence is reported as unproven rather than as a successful restore.

## Risks / Trade-offs

- [Hard links share the original inode] → Writes remain copy-on-replace only, write mode retains the Codex quiescence gate, and fallback copy is used whenever link creation fails.
- [A first metadata record is missing, oversized, or malformed] → Discovery reports the file as metadata-unavailable and does not inspect transcript content or select the file for mutation.
- [Progress event loss could trigger an idle timeout during healthy work] → Long byte-copy and rewrite loops emit chunk-level events, and the idle interval is comfortably above the maximum expected gap.
- [Derived after-counts could hide a failed mutation] → Results are returned only after targeted JSONL and SQLite verification confirms the plan.
- [Rollback after an unreported timeout cannot be proven] → ProviderSwitcher preserves the target config and marks metadata repair pending instead of claiming a safe rollback.

## Migration Plan

1. Add regression and performance-shape tests for one-pass planning, hard-link/copy fallback, atomic replacement, targeted verification, and progress emission.
2. Release the optimized CLI code and the progress-aware ProviderSwitcher together.
3. Validate both components against synthetic large files and isolated copied metadata; do not run the user's live retag.
4. Keep existing backups. After final user approval and Codex shutdown, use the exact failed-run backup to repair the current partial metadata before retrying the provider switch.

## Open Questions

None. The user has explicitly selected single-pass planning, hard-link-first backup, targeted verification, idle timeout, and UI progress as the required behavior.
