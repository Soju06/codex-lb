from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"


def _helm_template(*args: str) -> str:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering tests")
    completed = subprocess.run(
        ["helm", "template", "codex-lb", str(_CHART_DIR), *args],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def test_external_secrets_install_uses_startup_migration_and_skips_pre_install_hook() -> None:
    rendered = _helm_template(
        "--set",
        "externalSecrets.enabled=true",
        "--set",
        "externalSecrets.secretStoreRef.name=test-store",
        "--set",
        "migration.enabled=true",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "true"' in rendered
    assert '"helm.sh/hook": "pre-upgrade"' in rendered
    assert '"helm.sh/hook": "pre-install,pre-upgrade"' not in rendered


def test_external_secrets_upgrade_keeps_startup_migration_disabled_and_runs_hook() -> None:
    rendered = _helm_template(
        "--is-upgrade",
        "--set",
        "externalSecrets.enabled=true",
        "--set",
        "externalSecrets.secretStoreRef.name=test-store",
        "--set",
        "migration.enabled=true",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "false"' in rendered
    assert '"helm.sh/hook": "pre-upgrade"' in rendered


def test_chart_managed_secret_keeps_pre_install_hook_path() -> None:
    rendered = _helm_template(
        "--set",
        "externalSecrets.enabled=false",
        "--set",
        "migration.enabled=true",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "false"' in rendered
    assert '"helm.sh/hook": "pre-install,pre-upgrade"' in rendered
