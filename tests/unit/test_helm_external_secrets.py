from __future__ import annotations

import re
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


def _deployment_annotation(rendered: str, key: str) -> str:
    pattern = re.compile(rf"{re.escape(key)}: ([^\n]+)")
    match = pattern.search(rendered)
    assert match is not None, f"annotation {key} not found"
    return match.group(1).strip().strip('"')


def test_external_secrets_install_uses_startup_migration_and_skips_pre_install_hook() -> None:
    rendered = _helm_template(
        "--set",
        "externalSecrets.enabled=true",
        "--set",
        "externalSecrets.secretStoreRef.name=test-store",
        "--set",
        "migration.enabled=true",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "false"' in rendered
    assert '"helm.sh/hook": "post-install,pre-upgrade"' in rendered
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
    assert '"helm.sh/hook": "post-install,pre-upgrade"' in rendered


def test_chart_managed_secret_keeps_pre_install_hook_path() -> None:
    rendered = _helm_template(
        "--set",
        "externalSecrets.enabled=false",
        "--set",
        "migration.enabled=true",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "false"' in rendered
    assert '"helm.sh/hook": "post-install,pre-upgrade"' in rendered


def test_deployment_rolls_when_configmap_backed_env_changes() -> None:
    baseline = _helm_template()
    updated = _helm_template("--set", "config.logFormat=text")

    assert _deployment_annotation(baseline, "checksum/config") != _deployment_annotation(updated, "checksum/config")


def test_deployment_rolls_when_chart_managed_secret_changes() -> None:
    baseline = _helm_template()
    updated = _helm_template("--set", "postgresql.auth.password=changed-secret")

    assert _deployment_annotation(baseline, "checksum/secret") != _deployment_annotation(updated, "checksum/secret")


def test_deployment_can_enable_reloader_for_external_secret_changes() -> None:
    rendered = _helm_template(
        "--set",
        "auth.existingSecret=codex-lb-secrets",
        "--set",
        "rollout.reloader.enabled=true",
    )

    assert 'reloader.stakater.com/auto: "true"' in rendered
    assert 'configmap.reloader.stakater.com/reload: "codex-lb"' in rendered
    assert 'secret.reloader.stakater.com/reload: "codex-lb-secrets"' in rendered


def test_manual_rollout_token_changes_deployment_template() -> None:
    baseline = _helm_template("--set", "auth.existingSecret=codex-lb-secrets")
    updated = _helm_template(
        "--set",
        "auth.existingSecret=codex-lb-secrets",
        "--set",
        "rollout.manualToken=secret-rotation-2026-04-01",
    )

    assert "rollout-token" not in baseline
    assert 'rollout-token: "secret-rotation-2026-04-01"' in updated
