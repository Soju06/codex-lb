from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_WORKER = """
import os
import sys
from app.core.metrics.prometheus import accounts_total, accounts_available
for status in ('active', 'rate_limited', 'quota_exceeded', 'paused', 'reauth_required', 'deactivated'):
    accounts_total.labels(status=status).set(int(sys.argv[1]) if status == 'active' else 0)
accounts_available.set(int(sys.argv[1]))
print(os.getpid())
"""

_SCRAPE = """
import json
import sys
from prometheus_client.multiprocess import mark_process_dead
from app.core.metrics.prometheus import make_scrape_registry
if len(sys.argv) > 1:
    mark_process_dead(int(sys.argv[1]))
print(json.dumps([
    [s.name, s.labels, s.value]
    for metric in make_scrape_registry().collect()
    for s in metric.samples
    if s.name in {'codex_lb_accounts_total', 'codex_lb_accounts_available'}
]))
"""


def test_multiprocess_account_inventory_uses_newest_live_snapshot(tmp_path: Path) -> None:
    """Select the latest live pool snapshot, including zeroes and worker removal."""
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path)}

    def run(script: str, *args: str) -> str:
        """Run a fresh interpreter sharing the test's multiprocess metric files."""
        return subprocess.run(
            [sys.executable, "-c", script, *args],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout

    first_pid = run(_WORKER, "3").strip()
    second_pid = run(_WORKER, "2").strip()

    def assert_scrape(value: int, *args: str) -> None:
        """Check one pool snapshot with every status and no worker PID labels."""
        samples = json.loads(run(_SCRAPE, *args))
        assert len(samples) == 7
        for name, labels, actual in samples:
            assert "pid" not in labels
            expected = value if name == "codex_lb_accounts_available" or labels.get("status") == "active" else 0
            assert actual == expected

    assert_scrape(2)  # Neither the sum (5) nor the maximum (3).
    newest_pid = run(_WORKER, "0").strip()
    assert_scrape(0)  # A quiet/deleted pool must replace older nonzero values.
    assert_scrape(2, newest_pid)  # mark_process_dead discards the live* snapshot.
    assert_scrape(3, second_pid)
    assert json.loads(run(_SCRAPE, first_pid)) == []
