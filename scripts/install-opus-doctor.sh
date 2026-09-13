#!/usr/bin/env bash
set -euo pipefail

MODE="install"
case "${1:-}" in
  --print) MODE="print" ;;
  --uninstall) MODE="uninstall" ;;
  "") ;;
  *) echo "usage: $0 [--print | --uninstall]" >&2; exit 2 ;;
esac

TARGET_HOME="${AGENT_LB_DOCTOR_HOME:-$HOME}"
LABEL="com.agent-lb.opus-doctor"
PLIST="$TARGET_HOME/Library/LaunchAgents/$LABEL.plist"
STATE_DIR="${CLAUDE_LB_DOCTOR_STATE_DIR:-$TARGET_HOME/.agent-lb}"
LAUNCHER="${AGENT_LB_DOCTOR_LAUNCHER:-$TARGET_HOME/.agent-lb/runtime/agent-lb/clients/claude-lb-launch}"
NOTIFY="${AGENT_LB_DOCTOR_NOTIFY:-$TARGET_HOME/.agent-lb/bin/notify-alex.sh}"

if [[ "$MODE" == "uninstall" ]]; then
  launchctl bootout "gui/$UID" "$PLIST" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "removed $PLIST"
  exit 0
fi

if [[ "$MODE" == "install" && ! -x "$LAUNCHER" ]]; then
  echo "error: pinned internal-disk launcher is not executable: $LAUNCHER" >&2
  echo "set AGENT_LB_DOCTOR_LAUNCHER to the installed launcher path" >&2
  exit 1
fi
if [[ "$MODE" == "install" && "$LAUNCHER" == /Volumes/* ]]; then
  echo "error: refusing external-volume launcher for launchd: $LAUNCHER" >&2
  exit 1
fi
if [[ "$MODE" == "install" ]]; then
  LAUNCHER_DIR="$(cd "$(dirname "$LAUNCHER")" && pwd)"
  for sibling in fable agent-defs-doctor opus-runtime-doctor; do
    if [[ ! -x "$LAUNCHER_DIR/$sibling" ]]; then
      echo "error: required executable is missing: $LAUNCHER_DIR/$sibling" >&2
      exit 1
    fi
  done
fi

export OPUS_DOCTOR_PLIST="$PLIST"
export OPUS_DOCTOR_LABEL="$LABEL"
export OPUS_DOCTOR_LAUNCHER="$LAUNCHER"
export OPUS_DOCTOR_STATE_DIR="$STATE_DIR"
export OPUS_DOCTOR_NOTIFY="$NOTIFY"
export OPUS_DOCTOR_MODE="$MODE"
export OPUS_DOCTOR_HOME="$TARGET_HOME"
/usr/bin/python3 -c '
import os, plistlib
from pathlib import Path
home = Path(os.environ["OPUS_DOCTOR_HOME"])
state = Path(os.environ["OPUS_DOCTOR_STATE_DIR"])
local_bin = home / ".local/bin"
volta_bin = home / ".volta/bin"
payload = {
    "Label": os.environ["OPUS_DOCTOR_LABEL"],
    "ProgramArguments": [os.environ["OPUS_DOCTOR_LAUNCHER"], "--doctor-nightly"],
    "StartCalendarInterval": {"Hour": 3, "Minute": 20},
    "ProcessType": "Background",
    "WorkingDirectory": str(state),
    "EnvironmentVariables": {
        "CLAUDE_LB_DOCTOR_STATE_DIR": str(state),
        "CLAUDE_LB_DOCTOR_NOTIFY": os.environ["OPUS_DOCTOR_NOTIFY"],
        "PATH": f"{local_bin}:{volta_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    },
    "StandardOutPath": str(state / "doctor.log"),
    "StandardErrorPath": str(state / "doctor.log"),
}
encoded = plistlib.dumps(payload, sort_keys=True)
if os.environ["OPUS_DOCTOR_MODE"] == "print":
    print(encoded.decode("utf-8"), end="")
else:
    path = Path(os.environ["OPUS_DOCTOR_PLIST"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
'

if [[ "$MODE" == "print" ]]; then
  exit 0
fi

mkdir -p "$STATE_DIR"
launchctl bootout "gui/$UID" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$PLIST"
echo "installed $LABEL (03:20 nightly) using $LAUNCHER"
