"""Offline lifecycle programs embedded in exported Unix installers."""

UNIX_RESTORE_COMMON = r"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

names = ("config.toml", "auth.json", "codex-lb-models.json", "codex-lb-uninstall.sh")
codex_dir = Path(sys.argv[1]).resolve()
state_path = codex_dir / ".codex-lb-install-state.json"


def regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        sys.exit("Refusing a symlink or non-file: " + str(path))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_state():
    regular(state_path)
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        sys.exit("Invalid install state; client files were not replaced.")
    if (not isinstance(state, dict) or set(state) != {"version", "backup", "files"}
            or type(state["version"]) is not int or state["version"] != 1
            or not isinstance(state["backup"], str)
            or not re.fullmatch(r"backup-codex-lb-[A-Za-z0-9-]+", state["backup"])
            or not isinstance(state["files"], dict) or set(state["files"]) != set(names)):
        sys.exit("Invalid install state; client files were not replaced.")
    original = codex_dir / state["backup"]
    if original.is_symlink() or not original.is_dir():
        sys.exit("Original backup directory is missing or unsafe.")
    for name in names:
        path = original / name
        regular(path)
        expected = state["files"][name]
        if expected is None:
            valid = not path.exists()
        else:
            valid = (isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected)
                     and path.is_file() and digest(path) == expected)
        if not valid:
            sys.exit("Original backup is missing or changed: " + name)
    return state


state = load_state()
"""

UNIX_RECORD_STATE = (
    UNIX_RESTORE_COMMON
    + r"""
for name in names:
    regular(codex_dir / name)
backup_dir = Path(sys.argv[2])
if state is None:
    state = {
        "version": 1,
        "backup": backup_dir.name,
        "files": {name: digest(backup_dir / name) if (backup_dir / name).is_file() else None for name in names},
    }
(backup_dir / "state.new").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
"""
)

UNIX_UNINSTALL_SCRIPT = (
    r"""#!/usr/bin/env bash
# Offline restore for this Codex home. No API key or network required.
set -euo pipefail
umask 077
command -v python3 >/dev/null 2>&1 || { printf 'Install Python 3 before uninstalling.\n' >&2; exit 1; }
codex_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
python3 - "$codex_dir" <<'CODEX_LB_RESTORE'
"""
    + UNIX_RESTORE_COMMON
    + r"""
if state is None:
    print("No codex-lb install state; nothing to restore.")
    sys.exit(0)
for name in names:
    regular(codex_dir / name)
backup_dir = Path(tempfile.mkdtemp(prefix="backup-codex-lb-uninstall-", dir=codex_dir))
for name in (*names, state_path.name):
    path = codex_dir / name
    if path.is_file():
        shutil.copy2(path, backup_dir / name)
original = codex_dir / state["backup"]
# Stage every original before the first replacement. Keep state on any failure.
for name in names:
    if state["files"][name] is not None:
        shutil.copy2(original / name, backup_dir / (name + ".restore"))
for name in names:
    target = codex_dir / name
    if state["files"][name] is None:
        if target.exists():
            target.unlink()
    else:
        os.replace(backup_dir / (name + ".restore"), target)
state_path.unlink()
print("Original Codex setup restored. Restart your clients. Current-file backup: " + str(backup_dir))
CODEX_LB_RESTORE
"""
)
