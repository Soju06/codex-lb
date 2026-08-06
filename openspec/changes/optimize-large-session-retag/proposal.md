## Why

Provider switching can leave large Codex homes partially retagged when repeated full JSONL scans, full-copy backups, rewrites, and verification exceed an integrating supervisor's process limit. The retag path must scale with session volume while preserving exact rollback evidence and observable forward progress.

## What Changes

- Build the JSONL target set and provider counts from one metadata-only discovery pass.
- Back up matched JSONL files with same-volume hard links when safe, with a verified copy fallback when links are unavailable.
- Rewrite each matched JSONL once through an atomic replacement and verify only the metadata that was changed.
- Query each SQLite database once for planning, then verify only databases and rows selected by that plan.
- Emit machine-readable progress events that distinguish discovery, backup, JSONL rewrite, SQLite update, and verification work.
- Define progress-aware execution so an integrating UI can stop a genuinely stalled retag without imposing a fixed total-duration limit.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `runtime-portability`: Strengthen the Codex session retag contract for large homes, exact rollback, bounded passes, progress reporting, and targeted verification.

## Impact

- Affected code: `app/codex_sessions_retag.py`, `app/cli.py`, and retag unit/CLI tests.
- Affected consumer contract: any subprocess supervisor that parses the structured progress stream.
- Storage behavior: backup creation prefers same-volume hard links but retains a safe copy fallback and atomic destination replacement.
- No CLI command-line compatibility break is intended.
