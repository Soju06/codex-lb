from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from app.core.prestop import PRESTOP_REQUEST_TIMEOUT_SECONDS
from app.core.server import POST_DRAIN_CLEANUP_TIMEOUT_SECONDS

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"


def test_shutdown_defaults_satisfy_shared_deadline_and_cleanup_buffer() -> None:
    values = yaml.safe_load((_CHART_DIR / "values.yaml").read_text())

    drain_timeout = values["config"]["shutdownDrainTimeoutSeconds"]
    termination_grace = values["terminationGracePeriodSeconds"]

    assert 0 <= values["preStopSleepSeconds"] <= drain_timeout
    assert termination_grace == 65
    assert termination_grace >= (
        drain_timeout + PRESTOP_REQUEST_TIMEOUT_SECONDS + POST_DRAIN_CLEANUP_TIMEOUT_SECONDS + 5
    )


def test_deployment_template_uses_in_flight_driven_prestop_module_and_guards() -> None:
    template = (_CHART_DIR / "templates" / "deployment.yaml").read_text()

    assert "app.core.prestop" in template
    assert "--routing-dwell-seconds" in template
    assert ".Values.preStopSleepSeconds" in template
    assert "--drain-timeout-seconds" in template
    assert ".Values.config.shutdownDrainTimeoutSeconds" in template
    assert "shutdownDrainTimeoutSeconds must be greater than or equal to preStopSleepSeconds" in template
    assert "terminationGracePeriodSeconds must cover preStop start fallback" in template


def test_deployment_template_reserves_bounded_post_drain_cleanup_window() -> None:
    template = (_CHART_DIR / "templates" / "deployment.yaml").read_text()

    assert POST_DRAIN_CLEANUP_TIMEOUT_SECONDS == 25
    assert PRESTOP_REQUEST_TIMEOUT_SECONDS == 2
    assert "{{- $preStopStartFailureBudgetSeconds := 2 }}" in template
    assert "{{- $postDrainCleanupBufferSeconds := 30 }}" in template
    assert (
        "{{- $minimumTerminationGraceSeconds := add (add $shutdownDrainTimeoutSeconds "
        "$preStopStartFailureBudgetSeconds) $postDrainCleanupBufferSeconds }}"
    ) in template
    assert "{{- if lt (int .Values.terminationGracePeriodSeconds) $minimumTerminationGraceSeconds }}" in template

    minimum_grace = 30 + PRESTOP_REQUEST_TIMEOUT_SECONDS + POST_DRAIN_CLEANUP_TIMEOUT_SECONDS + 5
    assert minimum_grace == 62
    assert 60 < minimum_grace


def test_shutdown_values_are_typed_in_chart_schema() -> None:
    schema = json.loads((_CHART_DIR / "values.schema.json").read_text())
    properties = schema["properties"]

    assert properties["preStopSleepSeconds"] == {"type": "integer", "minimum": 0}
    assert properties["terminationGracePeriodSeconds"] == {"type": "integer", "minimum": 1}
    assert properties["config"]["properties"]["shutdownDrainTimeoutSeconds"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 300,
    }
