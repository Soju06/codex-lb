#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(realpath "$REPO_DIR")"
BIN_DIR="${AGENT_LB_CLIENT_BIN_DIR:-$HOME/.local/bin}"
POLICY_DIR="${AGENT_LB_POLICY_DIR:-$HOME/.agents/policy}"
USER_HOME="${AGENT_LB_USER_HOME:-$HOME}"
if [[ -n "${AGENT_LB_CLAUDE_BIN:-}" ]]; then
  CLAUDE_BIN="$AGENT_LB_CLAUDE_BIN"
elif command -v claude >/dev/null 2>&1; then
  CLAUDE_BIN="$(command -v claude)"
elif [[ -x "$USER_HOME/.local/bin/claude" ]]; then
  CLAUDE_BIN="$USER_HOME/.local/bin/claude"
else
  CLAUDE_BIN="claude"
fi
MODE="install"

case "${1:-}" in
  --print) MODE="print" ;;
  --uninstall) MODE="uninstall" ;;
  "") ;;
  *) echo "usage: $0 [--print | --uninstall]" >&2; exit 2 ;;
esac

# `cc` and `fable` use the canonical Fable driver. `opus` is the explicit
# genuine-1M Opus driver. The launcher and definition doctor are also public
# commands; runtime-only helpers remain siblings in the reviewed source bundle.
CLIENT_NAMES=(cc fable opus claude-lb-launch agent-defs-doctor)
POLICY_SOURCE="$REPO_DIR/config/coding-agents"
POLICY_INSTALLER="$POLICY_SOURCE/install-policy.py"
HOOK_TARGET="$USER_HOME/.claude/hooks/ccdex-gpt-only.sh"
RETIRED_CLIENTS=(ccdex ccdex-worker-mcp)

for name in "${CLIENT_NAMES[@]}"; do
  if [[ ! -x "$REPO_DIR/clients/$name" ]]; then
    echo "error: $REPO_DIR/clients/$name is missing or not executable" >&2
    exit 1
  fi
done
if [[ ! -f "$POLICY_SOURCE/ROUTING.md" || ! -x "$POLICY_SOURCE/verify-routing" || ! -x "$POLICY_INSTALLER" ]]; then
  echo "error: canonical coding-agent policy is incomplete at $POLICY_SOURCE" >&2
  exit 1
fi

remove_retired_artifacts() {
  for name in "${RETIRED_CLIENTS[@]}"; do
    target="$BIN_DIR/$name"
    if [[ -L "$target" && "$(readlink "$target")" == */clients/"$name" ]]; then
      rm "$target"
      echo "removed retired $target"
    fi
  done
  if [[ -L "$HOOK_TARGET" ]]; then
    rm "$HOOK_TARGET"
    echo "removed retired $HOOK_TARGET"
  fi
  "$CLAUDE_BIN" mcp remove --scope user ccdex-worker >/dev/null 2>&1 || true
}

if [[ "$MODE" == "print" ]]; then
  for name in "${CLIENT_NAMES[@]}"; do
    echo "link $BIN_DIR/$name -> $REPO_DIR/clients/$name"
  done
  echo "link $POLICY_DIR/coding-agents -> $POLICY_SOURCE"
  "$POLICY_INSTALLER" --home "$USER_HOME" --print
  echo "remove retired ccdex artifacts (clients, hook, MCP registration)"
  exit 0
fi

if [[ "$MODE" == "uninstall" ]]; then
  "$POLICY_INSTALLER" --home "$USER_HOME" --uninstall
  for name in "${CLIENT_NAMES[@]}"; do
    target="$BIN_DIR/$name"
    if [[ -L "$target" && "$(readlink "$target")" == "$REPO_DIR/clients/$name" ]]; then
      rm "$target"
      echo "removed $target"
    fi
  done
  policy_target="$POLICY_DIR/coding-agents"
  if [[ -L "$policy_target" && "$(readlink "$policy_target")" == "$POLICY_SOURCE" ]]; then
    rm "$policy_target"
    echo "removed $policy_target"
  fi
  remove_retired_artifacts
  exit 0
fi

"$POLICY_INSTALLER" --home "$USER_HOME"

mkdir -p "$BIN_DIR"
for name in "${CLIENT_NAMES[@]}"; do
  client_source="$REPO_DIR/clients/$name"
  target="$BIN_DIR/$name"
  if [[ -e "$target" && ! -L "$target" ]]; then
    backup="$target.pre-agent-lb"
    if [[ ! -e "$backup" ]]; then
      mv "$target" "$backup"
      echo "preserved existing $target at $backup"
    else
      echo "error: refusing to replace $target; backup already exists at $backup" >&2
      exit 1
    fi
  fi
  ln -sfn "$client_source" "$target"
  echo "linked $target -> $client_source"
done

mkdir -p "$POLICY_DIR"
policy_target="$POLICY_DIR/coding-agents"
if [[ -e "$policy_target" && ! -L "$policy_target" ]]; then
  policy_backup="$policy_target.pre-agent-lb"
  if [[ ! -e "$policy_backup" ]]; then
    mv "$policy_target" "$policy_backup"
    echo "preserved existing $policy_target at $policy_backup"
  else
    echo "error: refusing to replace $policy_target; backup already exists at $policy_backup" >&2
    exit 1
  fi
fi
ln -sfn "$POLICY_SOURCE" "$policy_target"
echo "linked $policy_target -> $POLICY_SOURCE"

remove_retired_artifacts
echo "removed retired ccdex artifacts where present"
