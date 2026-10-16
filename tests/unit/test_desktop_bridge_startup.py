from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.desktop_bridge import BridgeConfig, create_bridge_app

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("listen,upstream", [(0, 2455), (2456, 0), (65536, 2455), (True, 2455), (2455, 2455)])
def test_invalid_topology_fails_before_credentials_are_opened(tmp_path, listen, upstream):
    """Validate port types, ranges, and separation before accessing missing credential paths."""
    config = BridgeConfig(listen, upstream, tmp_path / "missing-home", tmp_path / "missing-token")
    with pytest.raises(ValueError, match="port") as error:
        create_bridge_app(config)
    assert str(tmp_path) not in str(error.value)


def test_missing_credentials_fail_before_binding(tmp_path):
    """Reject unavailable startup credentials without disclosing their paths."""
    config = BridgeConfig(2456, 2455, tmp_path, tmp_path / "missing-token")
    with pytest.raises(ValueError) as error:
        create_bridge_app(config)
    assert str(tmp_path) not in str(error.value)


def test_standalone_import_does_not_bootstrap_database_or_main_app():
    """Verify in a fresh interpreter that bridge imports leave server and database runtimes unloaded."""
    repo = Path(__file__).resolve().parents[2]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.desktop_bridge; "
            "assert 'app.main' not in sys.modules; "
            "assert not any(name.startswith('app.db') for name in sys.modules); "
            "assert 'app.core.runtime_logging' not in sys.modules",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
