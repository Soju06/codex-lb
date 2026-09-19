from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import main_checkout, worktree_root  # noqa: E402

IN_FLIGHT = (
    "app/core/config/settings.py",
    "app/modules/accounts/probes.py",
    "app/core/providers/registry.py",
)
BASE = "9c94744e6e176ec6b97247917718e770acbce636"


def _run(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True)


def main() -> None:
    root = worktree_root()
    shared = main_checkout()
    assert root.resolve() == Path("/Volumes/StudioExt/repos/of-wt-account-store")
    assert root.resolve() != shared.resolve()
    for path in IN_FLIGHT:
        shared_diff = _run(shared, "diff", "--", path)
        assert shared_diff.strip(), f"shared checkout lost dirty work for {path}"
        branch_diff = _run(root, "diff", BASE, "--", path)
        assert not branch_diff.strip(), f"branch touched in-flight file {path}"


if __name__ == "__main__":
    main()
