from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def test_dependencies_import_in_fresh_process() -> None:
    env = os.environ.copy()
    code = "import app.dependencies; import app.modules.rate_limit_reset_credits.api"

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
