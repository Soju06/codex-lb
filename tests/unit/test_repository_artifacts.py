from __future__ import annotations

import subprocess
from pathlib import Path


def test_codex_sandbox_symlink_is_not_tracked() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", ".codex"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )

    assert result.stdout == ""
