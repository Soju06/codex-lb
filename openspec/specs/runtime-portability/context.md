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

## Whole-home retag planning

See [the operator guide](../../../docs/session-retag.md) for `--progress-json`,
metadata bounds and recovery behavior. Whole-home retag caches one metadata plan
and grouped database counts, then verifies only matched targets. JSONL discovery
is capped at 64 KiB and recognizes leading canonical or legacy metadata; it does
not search transcript content. For example, a dry run against a 2 MiB transcript
reads the same bounded prefix as one against a 100 KiB transcript.

Clients must remain stopped during writes. Hard-link backups are preserved by
replacing the working file, but an external in-place writer can still change the
backup inode. Failure reports retained backups without promising automatic
rollback across files and databases. Targeted repair remains a separate command
proposal in PR #2323.

If a progress reader exits early, retag disables progress output and finishes the confirmed operation. For example, a supervisor can stop reading stderr without interrupting file writes. The CLI replaces the closed stderr stream to prevent Python from failing a second flush at shutdown. Stdout still reports the verified result.
