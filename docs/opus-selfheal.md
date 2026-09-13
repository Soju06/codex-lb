# Opus self-heal operations

## User commands

- `cc --doctor` runs definition repair and a foreground Opus runtime probe. It
  writes the latest receipt to `~/.agent-lb/doctor.json` and appends a compact
  entry to `~/.agent-lb/doctor.log`.
- Every normal `cc` launch runs the cached agent-definition lint/repair before
  Claude Code starts, then requests an asynchronous runtime probe. The probe is
  gated to at most once every six hours.
- Claude Code caches its session agent registry. After definitions or model
  routing change, start a new Claude Code session; an existing session does not
  reload the repaired registry.
- Set a nonempty `AGENT_LB_OPUS_MODEL` to override the canonical Opus model. The
  default is `claude-opus-5`.

A passing receipt proves that the tested route worked at that time. It does not
guarantee future provider capacity.

## Client-only rollout

This lane is client-only. Installers may copy the reviewed and tested client
files into the internal-disk runtime bundle without restarting agent-lb. A full
`sync-runtime.sh` is neither required nor recommended because it could deploy
unrelated dirty main-worktree changes.

For a reviewed source bundle, expose its commands with the existing installer:

```sh
AGENT_LB_CLIENT_BIN_DIR="$HOME/.local/bin" \
  /path/to/reviewed/agent-lb/scripts/install-claude-clients.sh
```

The launcher resolves runtime helpers from its own source-bundle directory, so
`opus-runtime-doctor` does not need a separate command symlink. Do not point the
installed links at an external-disk or unreviewed working tree for ongoing use.

## Nightly probe

Install the nightly job only after the reviewed client bundle is present on the
internal disk. Pin both the internal launcher and the notification program:

```sh
AGENT_LB_DOCTOR_LAUNCHER="$HOME/.agent-lb/runtime/agent-lb/clients/claude-lb-launch" \
AGENT_LB_DOCTOR_NOTIFY="$HOME/.agent-lb/bin/notify-alex.sh" \
  /path/to/reviewed/agent-lb/scripts/install-opus-doctor.sh
```

The nightly job records the same `doctor.json` receipt and `doctor.log` summary.
On failure it invokes the configured `CLAUDE_LB_DOCTOR_NOTIFY` program, with
deduplication; notification acceptance is recorded in the receipt.
