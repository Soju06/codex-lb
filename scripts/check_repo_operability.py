from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REQUIRED_FILES = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "bin/setup",
    "bin/test",
    "bin/dev",
    "bin/logs",
    "bin/worktree",
    "bin/check-operability",
)
EXECUTABLE_FILES = tuple(path for path in REQUIRED_FILES if path.startswith("bin/"))
INSTRUCTION_LINKS = {"CLAUDE.md": "AGENTS.md", ".claude": ".agents"}
AGENT_COMMAND_LINKS = ("bin/setup", "bin/test", "bin/dev", "bin/logs", "bin/worktree")


def check_repository(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.is_file():
            errors.append(f"missing canonical file: {relative}")

    for relative in EXECUTABLE_FILES:
        path = root / relative
        if path.exists() and not os.access(path, os.X_OK):
            errors.append(f"canonical command is not executable: {relative}")

    for relative, expected in INSTRUCTION_LINKS.items():
        path = root / relative
        if not path.is_symlink():
            errors.append(f"instruction compatibility path is not a symlink: {relative}")
        elif os.readlink(path) != expected:
            errors.append(f"incorrect symlink: {relative} -> {os.readlink(path)} (expected {expected})")

    agents = root / "AGENTS.md"
    if agents.is_file():
        content = agents.read_text(encoding="utf-8")
        for command in AGENT_COMMAND_LINKS:
            if command not in content:
                errors.append(f"AGENTS.md does not link canonical command: {command}")

    setup = root / "bin/setup"
    if setup.is_file():
        content = setup.read_text(encoding="utf-8")
        if "bun run build" not in content:
            errors.append("bin/setup does not build frontend assets required by bin/dev")

    dev = root / "bin/dev"
    if dev.is_file():
        content = dev.read_text(encoding="utf-8")
        if not re.search(r"(?:HOST=|--host[ '\"]+)127\.0\.0\.1", content):
            errors.append("bin/dev does not pin the service to 127.0.0.1")
        unsafe = re.search(r"--host[ '\"]+(?:0\.0\.0\.0|::)(?:[ '\"\n]|$)", content)
        if unsafe:
            errors.append("bin/dev contains an all-interface host binding")
        if ".local/logs/dev.log" not in content and 'LOG_DIR="$ROOT/.local/logs"' not in content:
            errors.append("bin/dev does not declare the checkout-local aggregate log")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check repository operability invariants.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    errors = check_repository(root)
    if errors:
        for error in errors:
            print(f"operability: FAIL: {error}", file=sys.stderr)
        return 1
    print(f"operability: PASS: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
