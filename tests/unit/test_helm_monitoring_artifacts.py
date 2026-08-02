"""Regression tests for Helm monitoring artifact queries."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"


def test_high_error_rate_alert_aggregates_request_series_before_division() -> None:
    prometheus_rule = (_CHART_DIR / "templates" / "prometheusrule.yaml").read_text()
    match = re.search(
        r"(?ms)^\s*- alert: CodexLBHighErrorRate\n\s+expr: \|\n(?P<expr>.*?)(?=^\s+for: 5m$)",
        prometheus_rule,
    )

    assert match is not None
    assert " ".join(match.group("expr").split()) == (
        '( sum by (namespace, job) ( rate(codex_lb_requests_total{status=~"5.."}[5m]) )'
        " / sum by (namespace, job) ( rate(codex_lb_requests_total[5m]) ) ) > 0.05"
    )


def test_grafana_error_rate_aggregates_request_series_before_division() -> None:
    dashboard = json.loads((_CHART_DIR / "dashboards" / "codex-lb.json").read_text())
    error_rate_panel = next(panel for panel in dashboard["panels"] if panel["title"] == "Error Rate (5xx)")

    assert error_rate_panel["targets"][0]["expr"] == (
        '(sum(rate(codex_lb_requests_total{namespace="$namespace",job=~"$job",status=~"5.."}[5m]))'
        ' or vector(0)) / sum(rate(codex_lb_requests_total{namespace="$namespace",job=~"$job"}[5m]))'
    )
