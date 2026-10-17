# Whole-home session retag

This command changes provider tags in local Codex session metadata. Its contract
is defined in [runtime portability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/runtime-portability).

Preview first:

```bash
codex-lb codex-sessions retag --from openai --to codex-lb \
  --codex-home /path/to/.codex --dry-run --progress-json
```

Stop Codex and Codex CLI before applying the same command with `--yes` instead of
`--dry-run`. Keep them stopped until verification finishes. The command leaves
`config.toml` and provider selection unchanged.

Discovery reads at most 64 KiB per JSONL file under `sessions/` and runs one
grouped provider-count query per eligible `state_*.sqlite` database. It recognizes
a leading `session_meta` record or consecutive leading legacy records with a
string `model_provider`. Transcript content is not searched for provider tags.
Malformed initial JSON or an oversized initial metadata record fails before writes.
After recognized legacy metadata, an incomplete record at the byte limit begins
the opaque tail and is left unchanged.
Files without recognized leading provider metadata do not become targets.
Archived sessions remain outside this command's existing scope.

Matched JSONL files are backed up with hard links, falling back to copies when
links are unavailable. Rewriting replaces the working file and preserves the
backup. SQLite backups use SQLite's snapshot API, including committed WAL data.
The existing SQLite copy-based write fallback is retained. Verification reads
only planned targets; unrelated files and databases are not rescanned.

`--progress-json` sends JSONL phase events to stderr while keeping the existing
human summary on stdout. Each event has `phase`, `completed`, and `total` fields.
The total is null until discovery enumerates the files. Phases are `discovery`,
`backup`, `rewrite`, `verification`, and `complete`. Counts refer to files and
databases, not bytes or rows. A phase emits a start event and per-item updates.
A single large file copy or database operation can take time between events.
Dry runs omit write phases. Errors produce a nonzero exit and a diagnostic;
consumers must not interpret missing `complete` as success.

Backups live under `backups/provider-retag/` in the selected home. A failure after
backup creation reports the retained path. Changes may be partial across files
and databases. There is no automatic rollback or cross-file transaction. Keep
the client stopped and use the retained originals for recovery. Hard links do
not protect against another process writing the old inode in place.
