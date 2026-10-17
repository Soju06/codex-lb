"""Regression tests for Helm monitoring artifact queries."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"
_POSTGRES_DATASOURCE_TYPE = "grafana-postgresql-datasource"


def _ttft_dashboard() -> dict:
    return json.loads((_CHART_DIR / "dashboards" / "ttft-breakdown.json").read_text())


def test_ttft_dashboard_declares_visible_single_select_postgres_datasource() -> None:
    dashboard = _ttft_dashboard()
    (datasource,) = dashboard["templating"]["list"]

    assert {
        "name": datasource["name"],
        "label": datasource["label"],
        "type": datasource["type"],
        "query": datasource["query"],
        "hide": datasource["hide"],
        "multi": datasource["multi"],
        "includeAll": datasource["includeAll"],
    } == {
        "name": "DS_SQL",
        "label": "PostgreSQL",
        "type": "datasource",
        "query": _POSTGRES_DATASOURCE_TYPE,
        "hide": 0,
        "multi": False,
        "includeAll": False,
    }


def test_ttft_dashboard_panels_bind_selected_postgres_datasource_uid() -> None:
    dashboard = _ttft_dashboard()

    assert len(dashboard["panels"]) == 4
    assert all(
        panel["datasource"]
        == {
            "type": _POSTGRES_DATASOURCE_TYPE,
            "uid": "${DS_SQL}",
        }
        for panel in dashboard["panels"]
    )


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


def test_grafana_latency_quantiles_aggregate_selected_bucket_series_by_le() -> None:
    latency_queries = {
        (dashboard_path.name, panel["title"], target["legendFormat"]): target["expr"]
        for dashboard_path in (_CHART_DIR / "dashboards").glob("*.json")
        for panel in json.loads(dashboard_path.read_text())["panels"]
        for target in panel.get("targets", [])
        if "histogram_quantile(" in target.get("expr", "")
    }

    assert latency_queries == {
        ("codex-lb.json", "Request Duration (p50/p95/p99)", "p50"): (
            "histogram_quantile(0.50, sum by (le) (rate("
            'codex_lb_request_duration_seconds_bucket{namespace="$namespace",job=~"$job"}[5m])))'
        ),
        ("codex-lb.json", "Request Duration (p50/p95/p99)", "p95"): (
            "histogram_quantile(0.95, sum by (le) (rate("
            'codex_lb_request_duration_seconds_bucket{namespace="$namespace",job=~"$job"}[5m])))'
        ),
        ("codex-lb.json", "Request Duration (p50/p95/p99)", "p99"): (
            "histogram_quantile(0.99, sum by (le) (rate("
            'codex_lb_request_duration_seconds_bucket{namespace="$namespace",job=~"$job"}[5m])))'
        ),
        ("codex-lb.json", "Upstream Request Duration (p99)", "p99"): (
            "histogram_quantile(0.99, sum by (le) (rate("
            'codex_lb_upstream_request_duration_seconds_bucket{namespace="$namespace",job=~"$job"}[5m])))'
        ),
    }


def test_high_latency_quantile_aggregates_bucket_series_by_scope_and_le() -> None:
    prometheus_rule = (_CHART_DIR / "templates" / "prometheusrule.yaml").read_text()
    match = re.search(
        r"(?ms)^\s*- alert: CodexLBHighLatency\n\s+expr: \|\n(?P<expr>.*?)(?=^\s+for: 5m$)",
        prometheus_rule,
    )

    assert match is not None
    assert " ".join(match.group("expr").split()) == (
        "histogram_quantile(0.99, sum by (namespace, job, le) ("
        " rate(codex_lb_request_duration_seconds_bucket[5m]) ) ) > 10"
    )
