# Repair a session's provider tags

Use these commands when one Codex session has different provider tags in its JSONL metadata and SQLite thread row. The [runtime-portability specification](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/runtime-portability/spec.md) defines the contract.

Close Codex and Codex CLI before repair. Preview the selected home:

```bash
codex-lb codex-sessions metadata-mismatches --provider codex-lb --codex-home /path/to/codex-home --json
```

The result lists mismatched session IDs and their observed tags. `unsupported_sessions` lists IDs with unsupported or missing tags. Preview does not change files or create backups. Discovery reads only the first metadata line of each JSONL file, capped at 1 MiB, under `sessions/` and `archived_sessions/`. It compares those IDs with `state_*.sqlite` databases that contain `threads.id` and `threads.model_provider`.

Repair only the selected session:

```bash
codex-lb codex-sessions repair-metadata --provider codex-lb --session-id SESSION_ID --codex-home /path/to/codex-home --yes --json
```

Repeat `--session-id` to select more sessions. Both commands accept `openai` or `codex-lb` as the target. A repair creates a fresh plan, so a previous preview does not authorize stale writes. Unknown IDs, unsupported tags, ambiguous JSON and oversized headers cause an error. The command preserves unrelated sessions, transcript bytes and `config.toml`; it does not select a provider for Codex.

With `--json`, stdout contains one result. Stderr contains newline-delimited progress records with `phase` and `completed`. Discovery reports items scanned; rewrite reports transcript bytes copied per file. Backup, SQLite and verification records report each completed item. Without `--json`, progress is plain text and the result is indented JSON.

## Recover after a failed repair

Backups live under `backups/session-metadata-repair/repair-*` in the selected home. JSONL paths mirror the original directories. JSONL backups use hard links when available, while repair replaces the original file atomically. SQLite backups use SQLite's snapshot API. A failure after backup reports the retained directory and exits unsuccessfully.

Repair is not one transaction across files and databases. Keep Codex closed, inspect the error and retained backups, and compare current files before restoring anything. Restore only affected files. Restoring a full SQLite snapshot also reverts unrelated rows to the snapshot time; preserve any newer work first. Restore with a copy or atomic replacement, not by editing a hard-linked backup in place. Automatic rollback is intentionally absent.

The existing `codex-sessions retag` command remains the whole-home provider migration. Its large-home discovery and progress improvements in [#1636](https://github.com/Soju06/codex-lb/issues/1636) remain separate work.
