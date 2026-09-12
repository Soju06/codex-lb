"""Strictly validate active OpenSpec changes touched by the GitHub event."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    event_name = os.environ["GITHUB_EVENT_NAME"]
    if event_name == "pull_request":
        pull_request = event["pull_request"]
        head = pull_request["head"]["sha"]
        base = git("merge-base", pull_request["base"]["sha"], head).strip()
    elif event_name == "push":
        base, head = event["before"], event["after"]
    elif event_name == "merge_group":
        base, head = event["merge_group"]["base_sha"], event["merge_group"]["head_sha"]
    else:
        raise ValueError(f"Unsupported event: {event_name}")
    if event_name == "push" and base == "0" * 40:
        paths = git("ls-tree", "-r", "--name-only", "-z", head, "--", "openspec/changes/")
    else:
        paths = git("diff", "--name-only", "-z", "--no-renames", base, head, "--", "openspec/changes/")
    changes = set()
    for path in paths.split("\0"):
        parts = path.split("/")
        if len(parts) >= 4 and parts[:2] == ["openspec", "changes"] and parts[2] != "archive":
            if Path(*parts[:3]).is_dir():
                changes.add(parts[2])
    for change in sorted(changes):
        subprocess.run(
            [
                "npx",
                "--yes",
                "@fission-ai/openspec@1.11.0",
                "validate",
                "--type",
                "change",
                "--strict",
                "--no-interactive",
                "--",
                change,
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
