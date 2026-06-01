## Why

Codex CLI stores the selected `model_provider` in local session metadata. After
switching a workstation from the upstream OpenAI provider to codex-lb, existing
threads tagged as `openai` can disappear from `codex resume` until their stored
provider tag is updated.

Manual JSONL or SQLite edits are easy to get wrong, especially across native,
WSL, and container runtimes, so codex-lb needs a documented CLI repair path.

## What Changes

- Add `codex-lb codex-sessions retag` for one-off local Codex session provider
  retagging.
- Support both Codex JSONL session files and newer `state_*.sqlite` thread
  metadata.
- Require dry-run preview or explicit write confirmation so non-interactive
  scripts do not rewrite local Codex state by accident.
- Create backups before mutating matched session files or state databases.

## Impact

Operators get a safer migration path for local Codex session metadata. The
command is local-only and does not change proxy request handling, account
routing, or server startup behavior.
