## 1. CLI contract

- [x] 1.1 Add the `codex-sessions retag` command under the existing `codex-lb` CLI.
- [x] 1.2 Support JSONL session metadata and `state_*.sqlite` thread metadata.
- [x] 1.3 Require `--dry-run` or explicit `--yes` before non-interactive writes.
- [x] 1.4 Create backups before rewriting matched session metadata.

## 2. Verification

- [x] 2.1 Add focused CLI/unit coverage for dry-run, apply, backup, storage-format, and error paths.
- [x] 2.2 Add an active OpenSpec delta for the new CLI behavior.
