#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(realpath "$REPO_DIR")"
BIN_DIR="${AGENT_LB_CLIENT_BIN_DIR:-$HOME/.local/bin}"
POLICY_DIR="${AGENT_LB_POLICY_DIR:-$HOME/.agents/policy}"
USER_HOME="${AGENT_LB_USER_HOME:-$HOME}"
RUNTIME_DIR="${AGENT_LB_CLIENT_RUNTIME_DIR:-$USER_HOME/.agent-lb/clients}"
CURRENT="$RUNTIME_DIR/current"
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
# commands; runtime-only helpers remain siblings in the installed bundle.
CLIENT_NAMES=(cc fable opus claude-lb-launch agent-defs-doctor)
BUNDLE_NAMES=("${CLIENT_NAMES[@]}" opus-runtime-doctor)
POLICY_SOURCE="$REPO_DIR/config/coding-agents"
POLICY_INSTALLER="$POLICY_SOURCE/install-policy.py"
HOOK_TARGET="$USER_HOME/.claude/hooks/ccdex-gpt-only.sh"
RETIRED_CLIENTS=(ccdex ccdex-worker-mcp)

for name in "${BUNDLE_NAMES[@]}"; do
  if [[ ! -x "$REPO_DIR/clients/$name" ]]; then
    echo "error: $REPO_DIR/clients/$name is missing or not executable" >&2
    exit 1
  fi
done
if [[ ! -f "$POLICY_SOURCE/ROUTING.md" || ! -x "$POLICY_SOURCE/verify-routing" || ! -x "$POLICY_INSTALLER" ]]; then
  echo "error: canonical coding-agent policy is incomplete at $POLICY_SOURCE" >&2
  exit 1
fi

prepare_bundle() {
  python3 - "$REPO_DIR" "$RUNTIME_DIR" "$MODE" "${BUNDLE_NAMES[@]}" <<'PY'
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

source, runtime = (Path(value).absolute() for value in sys.argv[1:3])
files = [Path("clients") / name for name in sys.argv[4:]]
files.append(Path("scripts/install-claude-clients.sh"))
files.extend(
    path.relative_to(source)
    for path in sorted((source / "config/coding-agents").rglob("*"))
    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
)

def digest(root):
    result = hashlib.sha256()
    for relative in files:
        path = root / relative
        content = path.read_bytes()
        result.update(f"{relative}\0{path.stat().st_mode & 0o777}\0{len(content)}\0".encode())
        result.update(content)
    return result.hexdigest()

version = digest(source)
destination = runtime / "versions" / version
if sys.argv[3] != "print":
    if destination.exists():
        if destination.is_symlink() or digest(destination) != version:
            sys.exit(f"error: installed client bundle differs from its version: {destination}")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".install-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "bundle"
            for relative in files:
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
            if digest(staged) != version:
                sys.exit("error: client source changed while creating the local bundle; retry installation")
            try:
                os.rename(staged, destination)
            except FileExistsError:
                if destination.is_symlink() or digest(destination) != version:
                    raise
print(destination)
PY
}

preflight_link() {
  local target="$1" source="$2"
  if [[ -L "$target" && "$(readlink "$target")" == "$source" ]]; then
    return
  fi
  if [[ -e "$target" || -L "$target" ]]; then
    if [[ -e "$target.pre-agent-lb" || -L "$target.pre-agent-lb" ]]; then
      echo "error: refusing to replace $target; backup already exists at $target.pre-agent-lb" >&2
      exit 1
    fi
  fi
}

install_link() {
  local target="$1" source="$2"
  if [[ -L "$target" && "$(readlink "$target")" == "$source" ]]; then
    return
  fi
  if [[ -e "$target" || -L "$target" ]]; then
    mv "$target" "$target.pre-agent-lb"
    echo "preserved existing $target at $target.pre-agent-lb"
  fi
  ln -s "$source" "$target"
  echo "linked $target -> $source"
}

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
  bundle="$(prepare_bundle)"
  echo "copy client bundle to $bundle"
  echo "link $CURRENT -> $bundle"
  for name in "${CLIENT_NAMES[@]}"; do
    echo "link $BIN_DIR/$name -> $CURRENT/clients/$name"
  done
  echo "link $POLICY_DIR/coding-agents -> $CURRENT/config/coding-agents"
  "$POLICY_INSTALLER" --home "$USER_HOME" --print
  echo "remove retired ccdex artifacts (clients, hook, MCP registration)"
  exit 0
fi

if [[ "$MODE" == "uninstall" ]]; then
  "$POLICY_INSTALLER" --home "$USER_HOME" --uninstall
  for name in "${CLIENT_NAMES[@]}"; do
    target="$BIN_DIR/$name"
    if [[ -L "$target" && ( "$(readlink "$target")" == "$CURRENT/clients/$name" || "$(readlink "$target")" == "$REPO_DIR/clients/$name" ) ]]; then
      rm "$target"
      echo "removed $target"
    fi
  done
  policy_target="$POLICY_DIR/coding-agents"
  if [[ -L "$policy_target" && ( "$(readlink "$policy_target")" == "$CURRENT/config/coding-agents" || "$(readlink "$policy_target")" == "$POLICY_SOURCE" ) ]]; then
    rm "$policy_target"
    echo "removed $policy_target"
  fi
  remove_retired_artifacts
  exit 0
fi

for name in "${CLIENT_NAMES[@]}"; do
  preflight_link "$BIN_DIR/$name" "$CURRENT/clients/$name"
done
preflight_link "$POLICY_DIR/coding-agents" "$CURRENT/config/coding-agents"
if [[ -e "$CURRENT" || -L "$CURRENT" ]]; then
  if [[ ! -L "$CURRENT" || "$(readlink "$CURRENT")" != "$RUNTIME_DIR/versions/"* ]]; then
    echo "error: refusing to replace unmanaged runtime path $CURRENT" >&2
    exit 1
  fi
fi
"$POLICY_INSTALLER" --home "$USER_HOME" --print >/dev/null
bundle="$(prepare_bundle)"
"$bundle/config/coding-agents/install-policy.py" --home "$USER_HOME"

# A launcher resolves its version once; retain old bundles for its child helpers.
python3 - "$bundle" "$CURRENT" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

source, target = sys.argv[1:]
with tempfile.TemporaryDirectory(prefix=".activate-", dir=Path(target).parent) as temporary:
    link = Path(temporary) / "current"
    link.symlink_to(source)
    os.replace(link, target)
PY
echo "activated client bundle $bundle"

mkdir -p "$BIN_DIR" "$POLICY_DIR"
for name in "${CLIENT_NAMES[@]}"; do
  install_link "$BIN_DIR/$name" "$CURRENT/clients/$name"
done
install_link "$POLICY_DIR/coding-agents" "$CURRENT/config/coding-agents"

remove_retired_artifacts
echo "removed retired ccdex artifacts where present"
