# Runtime Portability Context

See `openspec/specs/runtime-portability/spec.md` for normative requirements.

## Codex Session Retagging

`codex resume` filters sessions by `model_provider`. Sessions created before
switching to codex-lb may still be tagged as `openai`, so they will not appear
until the stored provider tag is updated.

Use the built-in command instead of editing Codex files by hand:

```bash
# Preview what will change first.
codex-lb codex-sessions retag --from openai --to codex-lb --dry-run

# Then close Codex/Codex CLI and apply the retag.
codex-lb codex-sessions retag --from openai --to codex-lb --yes
```

The command updates both Codex storage formats when they exist: JSONL session
files under `~/.codex/sessions` and `state_*.sqlite` thread rows created by
newer Codex CLI versions. It uses Python's built-in SQLite support, creates a
backup under `~/.codex/backups/provider-retag/`, and refuses non-interactive
writes unless `--yes` is provided.

On native Windows, macOS, Linux, and WSL, use `--codex-home PATH` if your Codex
data directory is not detected. In WSL, autodetect only considers the current
Windows `USERPROFILE`; pass `--codex-home /mnt/c/Users/<name>/.codex` to retag
another Windows profile explicitly.

To switch back, reverse the providers:

```bash
codex-lb codex-sessions retag --from codex-lb --to openai --dry-run
codex-lb codex-sessions retag --from codex-lb --to openai --yes
```

For Docker, mount your Codex data directory only for this one-off command:

```bash
docker run --rm \
  -v ~/.codex:/codex-home \
  ghcr.io/soju06/codex-lb:latest \
  codex-lb codex-sessions retag --from openai --to codex-lb \
    --codex-home /codex-home --dry-run

docker run --rm \
  -v ~/.codex:/codex-home \
  ghcr.io/soju06/codex-lb:latest \
  codex-lb codex-sessions retag --from openai --to codex-lb \
    --codex-home /codex-home --yes
```

## Targeted session metadata repair

The targeted CLI implements the repair slice of #1636. Whole-home retag optimization remains outside that slice. See [the operator guide](../../../docs/session-metadata.md) for commands and recovery limits.

For example, JSONL tag `openai` and SQLite tag `codex-lb` for the same ID produce a mismatch. `repair-metadata --provider codex-lb --session-id ID --yes` changes only that selected session's tags. It does not change `config.toml` or provider selection. Discovery also includes archived sessions and supports legacy top-level metadata headers.

The CLI builds a fresh plan for each call. It caps header reads at 1 MiB, rejects ambiguous metadata and unsupported selected tags, preserves opaque transcript bodies, takes backups before mutation and checks selected targets afterward. Codex must be closed. File identity and provider comparisons detect stale plans but cannot make independent files and databases one transaction. Backups survive failure; automatic rollback could overwrite newer work and is not attempted.
