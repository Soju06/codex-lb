"""Regression tests for configurable Helm startup probe timing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"

_DEFAULT_STARTUP_PROBE = {
    "httpGet": {"path": "/health/startup", "port": "http"},
    "initialDelaySeconds": 5,
    "periodSeconds": 2,
    "timeoutSeconds": 1,
    "failureThreshold": 30,
    "successThreshold": 1,
}
_DEFAULT_READINESS_PROBE = {
    "httpGet": {"path": "/health/ready", "port": "http"},
    "initialDelaySeconds": 10,
    "periodSeconds": 5,
    "failureThreshold": 3,
}
_DEFAULT_LIVENESS_PROBE = {
    "httpGet": {"path": "/health/live", "port": "http"},
    "initialDelaySeconds": 15,
    "periodSeconds": 10,
    "failureThreshold": 3,
}


def _render_container(*args: str) -> dict:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering tests")

    completed = subprocess.run(
        [
            "helm",
            "template",
            "codex-lb",
            str(_CHART_DIR),
            "--show-only",
            "templates/deployment.yaml",
            *args,
        ],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    (stateful_set,) = [document for document in yaml.safe_load_all(completed.stdout) if document]
    return stateful_set["spec"]["template"]["spec"]["containers"][0]


def test_default_startup_probe_preserves_existing_effective_behavior() -> None:
    container = _render_container()

    assert container["startupProbe"] == _DEFAULT_STARTUP_PROBE
    assert container["readinessProbe"] == _DEFAULT_READINESS_PROBE
    assert container["livenessProbe"] == _DEFAULT_LIVENESS_PROBE


def test_startup_probe_failure_threshold_can_be_overridden() -> None:
    container = _render_container("--set", "startupProbe.failureThreshold=90")

    assert container["startupProbe"] == {
        **_DEFAULT_STARTUP_PROBE,
        "failureThreshold": 90,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initialDelaySeconds", 17),
        ("periodSeconds", 7),
        ("timeoutSeconds", 4),
    ],
)
def test_each_additional_startup_probe_value_can_be_overridden(
    field: str,
    value: int,
) -> None:
    container = _render_container("--set", f"startupProbe.{field}={value}")

    assert container["startupProbe"] == {
        **_DEFAULT_STARTUP_PROBE,
        field: value,
    }
    assert container["readinessProbe"] == _DEFAULT_READINESS_PROBE
    assert container["livenessProbe"] == _DEFAULT_LIVENESS_PROBE


def test_startup_probe_success_threshold_rejects_kubernetes_invalid_value() -> None:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering tests")

    completed = subprocess.run(
        [
            "helm",
            "template",
            "codex-lb",
            str(_CHART_DIR),
            "--set",
            "startupProbe.successThreshold=2",
        ],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "/startupProbe/successThreshold" in completed.stderr
    assert "maximum: got 2, want 1" in completed.stderr
