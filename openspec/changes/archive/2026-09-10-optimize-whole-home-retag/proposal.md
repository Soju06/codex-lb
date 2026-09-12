## Why

Issue #1636 accepts bounded whole-home retag planning and progress. Current retag repeatedly scans transcripts and counts SQLite rows, with no structured progress. Targeted repair is covered separately by PR #2323.

## What Changes

- Discover JSONL metadata within a bounded prefix and query each state database once for grouped provider counts.
- Cache the plan, back up before writes using hard links with copy fallback, and verify only planned targets.
- Add optional JSON progress on stderr while preserving the existing human summary and confirmation.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `runtime-portability`: bounded whole-home retag planning, backup, verification and progress.

## Impact

Standalone retag CLI, fixture tests and operator docs. No runtime settings or dependencies. No live home operation. This is partial #1636 coverage without the targeted commands from #2323.
