from __future__ import annotations

from pathlib import Path

import yaml


def test_chart_kube_version_floor_is_1_32() -> None:
    chart = yaml.safe_load(Path("deploy/helm/codex-lb/Chart.yaml").read_text(encoding="utf-8"))
    assert chart["kubeVersion"] == ">=1.32.0-0"


def test_chart_readme_documents_modern_support_policy() -> None:
    readme = Path("deploy/helm/codex-lb/README.md").read_text(encoding="utf-8")
    assert "Kubernetes 1.32+" in readme
    assert "Validation baseline in CI and smoke installs: `1.35`" in readme


def test_ci_uses_1_32_minimum_and_1_35_baseline() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "kubeconform (K8s 1.32.0)" in workflow
    assert "kubeconform (K8s 1.35.0)" in workflow
    assert "-kubernetes-version 1.32.0" in workflow
    assert "-kubernetes-version 1.35.0" in workflow
    assert "kind create cluster --name codex-lb-smoke --image kindest/node:v1.35.0 --wait 120s" in workflow
    assert "kubeconform (K8s 1.25.0)" not in workflow
    assert "kubeconform (K8s 1.28.0)" not in workflow
    assert "kubeconform (K8s 1.31.0)" not in workflow
