"""Pin env-file discovery: module root by default, explicit CODEX_LB_ENV_FILE override.

Settings resolves ``ENV_FILES`` at import time, so every case runs a fresh
interpreter with a controlled environment instead of reloading modules
in-process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROBE_CODE = (
    "import json; "
    "from app.core.config.settings import Settings; "
    "settings = Settings(); "
    "print(json.dumps([settings.log_format, settings.leader_election_enabled]))"
)
_DEFAULTS = ["text", True]


def _probe_settings(cwd: Path, extra_env: dict[str, str] | None = None) -> list[object]:
    environ = {name: value for name, value in os.environ.items() if not name.startswith("CODEX_LB_")}
    environ["PYTHONPATH"] = str(_REPO_ROOT)
    if extra_env:
        environ.update(extra_env)
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE_CODE],
        cwd=cwd,
        env=environ,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _skip_if_checkout_has_env_files() -> None:
    for name in (".env", ".env.local"):
        if (_REPO_ROOT / name).exists():
            pytest.skip(f"repository checkout has {name}; module-root discovery is ambient here")


def test_launch_directory_env_files_are_not_loaded(tmp_path: Path) -> None:
    """Launching from a directory with unrelated env files must not load them.

    Discovery is anchored at the module root; a ``Path.cwd()`` anchor would
    silently apply another project's ``.env`` to e.g. ``uvx codex-lb`` run
    from that project's directory.
    """
    _skip_if_checkout_has_env_files()
    (tmp_path / ".env").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("CODEX_LB_LEADER_ELECTION_ENABLED=false\n", encoding="utf-8")

    assert _probe_settings(tmp_path) == _DEFAULTS


def test_env_file_override_loads_explicit_files(tmp_path: Path) -> None:
    """CODEX_LB_ENV_FILE points relocated installs (the Nix wrapper) at env files."""
    env_dir = tmp_path / "config"
    env_dir.mkdir()
    (env_dir / ".env").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")
    (env_dir / ".env.local").write_text("CODEX_LB_LEADER_ELECTION_ENABLED=false\n", encoding="utf-8")
    launch_dir = tmp_path / "elsewhere"
    launch_dir.mkdir()

    override = os.pathsep.join([str(env_dir / ".env"), str(env_dir / ".env.local")])

    assert _probe_settings(launch_dir, {"CODEX_LB_ENV_FILE": override}) == ["json", False]


def test_env_file_override_tolerates_missing_files(tmp_path: Path) -> None:
    """The Nix wrapper always exports launch-dir paths; absent files are no-ops."""
    override = os.pathsep.join([str(tmp_path / ".env"), str(tmp_path / ".env.local")])

    assert _probe_settings(tmp_path, {"CODEX_LB_ENV_FILE": override}) == _DEFAULTS


def test_blank_env_file_override_falls_back_to_module_root(tmp_path: Path) -> None:
    _skip_if_checkout_has_env_files()
    (tmp_path / ".env").write_text("CODEX_LB_LOG_FORMAT=json\n", encoding="utf-8")

    assert _probe_settings(tmp_path, {"CODEX_LB_ENV_FILE": "   "}) == _DEFAULTS
