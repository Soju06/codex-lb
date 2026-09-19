# Runtime Portability Context

See `openspec/specs/runtime-portability/spec.md` for normative requirements.

## Local Claude client installation

`scripts/install-claude-clients.sh` copies the public commands, sibling runtime
doctors, coding-agent policy, and installer into
`~/.agent-lb/clients/versions/<content-sha256>/`. Public commands and the policy
link point through `~/.agent-lb/clients/current`, which switches atomically only
after the copied bundle is complete. Older versions remain available for active
processes and rollback. Reinstall after a source change to update the commands.

This removes the installed client's dependency on an external source volume.
It does not grant access to that volume or make an unavailable project readable.
Claude Code and Python must still be installed locally. The repository-oriented
`verify-routing` script still requires its development test environment.

Preview and install from a readable checkout:

```bash
./scripts/install-claude-clients.sh --print
./scripts/install-claude-clients.sh
```

`AGENT_LB_CLIENT_RUNTIME_DIR` selects a different local bundle root. Existing
`AGENT_LB_USER_HOME`, `AGENT_LB_CLIENT_BIN_DIR`, and `AGENT_LB_POLICY_DIR`
overrides still apply. Unmanaged replaced commands and policy links are backed
up with `.pre-agent-lb`; a conflicting backup stops installation before managed
targets change. Bundles and backups are retained on uninstall:

```bash
~/.agent-lb/clients/current/scripts/install-claude-clients.sh --uninstall
```

The launcher separately checks whether the current directory is readable and
raises a low process file-descriptor soft limit within the inherited hard limit.
An inaccessible directory gets a directory-specific error, without changing the
project. Process-limit adjustment requires no administrator access and does not
change launchd or kernel limits. A large machine can still have an unavailable
working directory or a low inherited process limit.

## Codex Session Retagging

`codex resume` filters sessions by `model_provider`. Sessions created before
switching to agent-lb may still be tagged as `openai`, so they will not appear
until the stored provider tag is updated.

Use the built-in command instead of editing Codex files by hand:

```bash
# Preview what will change first.
agent-lb codex-sessions retag --from openai --to agent-lb --dry-run

# Then close Codex/Codex CLI and apply the retag.
agent-lb codex-sessions retag --from openai --to agent-lb --yes
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
agent-lb codex-sessions retag --from agent-lb --to openai --dry-run
agent-lb codex-sessions retag --from agent-lb --to openai --yes
```

For Docker, mount your Codex data directory only for this one-off command:

```bash
docker run --rm \
  -v ~/.codex:/codex-home \
  ghcr.io/aneym/agent-lb:1.20.0-beta.3 \
  agent-lb codex-sessions retag --from openai --to agent-lb \
    --codex-home /codex-home --dry-run

docker run --rm \
  -v ~/.codex:/codex-home \
  ghcr.io/aneym/agent-lb:1.20.0-beta.3 \
  agent-lb codex-sessions retag --from openai --to agent-lb \
    --codex-home /codex-home --yes
```
