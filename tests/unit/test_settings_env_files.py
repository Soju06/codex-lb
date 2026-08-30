from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_settings_load_env_files_from_launch_directory(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("CODEX_LB_LEADER_ELECTION_ENABLED=false\n", encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[2]
    environ = {name: value for name, value in os.environ.items() if not name.startswith("CODEX_LB_")}
    environ["PYTHONPATH"] = str(repo_root)
    code = (
        "import json; "
        "from app.core.config.settings import Settings; "
        "settings = Settings(); "
        "print(json.dumps([settings.log_format, settings.leader_election_enabled]))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environ,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == ["json", False]
