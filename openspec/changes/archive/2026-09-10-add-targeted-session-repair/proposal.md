## Why

#1636 accepts a way to repair one session whose JSONL and SQLite provider tags disagree. Current main offers only whole-home retagging, which also changes unrelated sessions.

## What Changes

- Add read-only `metadata-mismatches` and explicitly confirmed `repair-metadata` commands for selected session IDs and supported provider tags.
- Back up planned files and databases before mutation, preserve unrelated data, verify selected targets, and emit JSON progress.
- This is a partial implementation of #1636. Whole-home retag discovery and progress optimization remain separate work.

## Capabilities

### New Capabilities

### Modified Capabilities

- `runtime-portability`: targeted session metadata preview and repair.

## Impact

Local CLI and Codex session files only. No new dependencies, server settings, provider-selection changes, or dashboard changes. The CLI is the regression-test interface accepted in #1636.
