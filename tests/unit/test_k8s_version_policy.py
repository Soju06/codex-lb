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


def test_ci_excludes_helm_and_k8s_validation_jobs() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "kubeconform (K8s 1.32.0)" not in workflow
    assert "kubeconform (K8s 1.35.0)" not in workflow
    assert "-kubernetes-version 1.32.0" not in workflow
    assert "-kubernetes-version 1.35.0" not in workflow
    assert "kind create cluster --name codex-lb-smoke --image kindest/node:v1.35.0 --wait 120s" not in workflow
