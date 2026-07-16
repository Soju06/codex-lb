"""Regression tests for the multi-replica deployment artifacts.

Covers the artifact-level defects that shipped broken multi-replica deployments:
- values-prod.yaml enabling a fail-closed NetworkPolicy without an ingress-controller allow rule
- nginx ingress annotations rendering as an incoherent split set
- the responses sticky default relying on an admission-rejected configuration-snippet
- chart README bridge examples that fail application Settings validation
- static-ring values that crashloop pods instead of failing at render time
- kind smoke never exercising the default two-replica topology
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from app.core.config.settings import Settings

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"
_CHART_README = _CHART_DIR / "README.md"
_SMOKE_SCRIPT = _REPO_ROOT / "scripts" / "helm-kind-smoke.sh"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_HTTP_PORT = 2455
_DEPENDENCY_BUILD_COMPLETE = False


def _ensure_chart_dependencies() -> None:
    global _DEPENDENCY_BUILD_COMPLETE
    if _DEPENDENCY_BUILD_COMPLETE:
        return

    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering tests")

    subprocess.run(
        ["helm", "dependency", "build", str(_CHART_DIR)],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    _DEPENDENCY_BUILD_COMPLETE = True


def _helm_template(*args: str) -> str:
    _ensure_chart_dependencies()
    completed = subprocess.run(
        ["helm", "template", "codex-lb", str(_CHART_DIR), *args],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _helm_template_error(*args: str) -> str:
    _ensure_chart_dependencies()
    completed = subprocess.run(
        ["helm", "template", "codex-lb", str(_CHART_DIR), *args],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0, "expected helm template to fail"
    return completed.stderr


_NOTES_WRAPPER_TEMPLATE = """\
apiVersion: v1
kind: ConfigMap
metadata:
  name: rendered-notes
data:
  notes: {{ include "codex-lb/templates/NOTES.txt" . | toJson }}
"""


def _helm_notes(*args: str) -> str:
    """Render the chart NOTES.txt without a Kubernetes cluster.

    ``helm install --dry-run=client`` still requires a reachable cluster
    (the install action checks API-server reachability before rendering),
    so it fails on CI runners that have no kubeconfig. Instead, render the
    NOTES.txt template through ``helm template`` by including it from a
    wrapper ConfigMap manifest in a throwaway copy of the chart.
    """
    _ensure_chart_dependencies()
    with tempfile.TemporaryDirectory() as tmp_dir:
        chart_copy = Path(tmp_dir) / "codex-lb"
        shutil.copytree(_CHART_DIR, chart_copy)
        (chart_copy / "templates" / "zz-rendered-notes.yaml").write_text(_NOTES_WRAPPER_TEMPLATE)
        completed = subprocess.run(
            [
                "helm",
                "template",
                "codex-lb",
                str(chart_copy),
                "--show-only",
                "templates/zz-rendered-notes.yaml",
                *args,
            ],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    (document,) = _helm_documents(completed.stdout)
    notes = document["data"]["notes"]
    assert isinstance(notes, str)
    return notes


def _helm_documents(rendered: str) -> list[dict]:
    return [document for document in yaml.safe_load_all(rendered) if document]


def _prod_overlay_args(*args: str) -> tuple[str, ...]:
    return (
        "-f",
        str(_CHART_DIR / "values-prod.yaml"),
        "--set",
        "externalSecrets.secretStoreRef.name=test-store",
        *args,
    )


def _staging_overlay_args(*args: str) -> tuple[str, ...]:
    return (
        "-f",
        str(_CHART_DIR / "values-staging.yaml"),
        "--set",
        "externalDatabase.url=postgresql+asyncpg://test:test@localhost/test",
        *args,
    )


def _http_port_rules(policy: dict) -> list[dict]:
    rules = []
    for rule in policy["spec"].get("ingress", []):
        ports = rule.get("ports", [])
        if any(port.get("port") == _HTTP_PORT for port in ports):
            rules.append(rule)
    return rules


def test_prod_overlay_network_policy_allows_ingress_controller_on_http_port() -> None:
    rendered = _helm_template(*_prod_overlay_args("--show-only", "templates/networkpolicy.yaml"))
    (policy,) = _helm_documents(rendered)

    http_rules = _http_port_rules(policy)
    assert http_rules, "no NetworkPolicy ingress rule opens the HTTP port"

    controller_selectors = [
        peer["namespaceSelector"] for rule in http_rules for peer in rule.get("from", []) if "namespaceSelector" in peer
    ]
    assert {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}} in controller_selectors

    # Fail-closed invariant: no allow-all namespaceSelector on the HTTP port.
    assert {} not in controller_selectors


def test_notes_warn_when_network_policy_denies_ingress_controller() -> None:
    notes = _helm_notes(
        "--set",
        "postgresql.auth.password=test-password",
        "--set",
        "networkPolicy.enabled=true",
        "--set",
        "ingress.enabled=true",
    )

    assert "WARNING" in notes
    assert "DENIES all ingress-controller traffic to port" in notes
    assert "networkPolicy.ingressNSMatchLabels" in notes


def test_notes_warning_absent_when_ingress_allowlist_is_configured() -> None:
    # ServiceMonitor/PrometheusRule CRDs are not resolvable in a clusterless dry run.
    notes = _helm_notes(
        *_prod_overlay_args(
            "--set",
            "metrics.serviceMonitor.enabled=false",
            "--set",
            "metrics.prometheusRule.enabled=false",
        )
    )

    assert "DENIES all ingress-controller traffic" not in notes


def test_staging_and_prod_overlays_render_coherent_nginx_annotation_set() -> None:
    for overlay_args in (_staging_overlay_args(), _prod_overlay_args()):
        rendered = _helm_template(*overlay_args, "--show-only", "templates/ingress.yaml")
        documents = _helm_documents(rendered)
        assert len(documents) == 2, "expected the main and responses Ingress"

        for document in documents:
            annotations = document["metadata"]["annotations"]
            assert annotations["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"
            assert annotations["nginx.ingress.kubernetes.io/proxy-request-buffering"] == "off"
            assert annotations["nginx.ingress.kubernetes.io/proxy-read-timeout"] == "3600"
            assert annotations["nginx.ingress.kubernetes.io/proxy-send-timeout"] == "3600"
            assert annotations["nginx.ingress.kubernetes.io/proxy-body-size"] == "50m"
            assert "nginx.ingress.kubernetes.io/upstream-hash-by" in annotations
            assert "nginx.ingress.kubernetes.io/configuration-snippet" not in annotations


def test_responses_sticky_default_is_snippet_free_session_hash() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.auth.password=test-password",
        "--set",
        "ingress.enabled=true",
        "--set",
        "ingress.nginx.enabled=true",
        "--show-only",
        "templates/ingress.yaml",
    )

    responses = next(
        document for document in _helm_documents(rendered) if document["metadata"]["name"].endswith("-responses")
    )
    annotations = responses["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/upstream-hash-by"] == "$http_x_codex_session_id$http_authorization"
    assert "nginx.ingress.kubernetes.io/configuration-snippet" not in annotations


def test_ingress_without_nginx_flag_renders_no_nginx_annotations() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.auth.password=test-password",
        "--set",
        "ingress.enabled=true",
        "--show-only",
        "templates/ingress.yaml",
    )

    assert rendered.count("kind: Ingress") == 2
    assert "nginx.ingress.kubernetes.io/" not in rendered


def test_explicit_configuration_snippet_still_renders() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.auth.password=test-password",
        "--set",
        "ingress.enabled=true",
        "--set",
        "ingress.nginx.enabled=true",
        "--set-string",
        'ingress.responses.nginx.configurationSnippet=set $codex_key "$http_authorization";',
        "--show-only",
        "templates/ingress.yaml",
    )

    assert "nginx.ingress.kubernetes.io/configuration-snippet:" in rendered
    assert 'set $codex_key "$http_authorization";' in rendered


def test_static_ring_with_autoscaling_fails_at_render_time() -> None:
    stderr = _helm_template_error(
        "--set",
        "postgresql.auth.password=test-password",
        "--set-string",
        "config.sessionBridgeInstanceRing=codex-lb-workload-0\\,codex-lb-workload-1",
        "--set",
        "autoscaling.enabled=true",
    )

    assert "incompatible with autoscaling.enabled=true" in stderr


def test_static_ring_smaller_than_replica_count_fails_at_render_time() -> None:
    stderr = _helm_template_error(
        "--set",
        "postgresql.auth.password=test-password",
        "--set-string",
        "config.sessionBridgeInstanceRing=codex-lb-workload-0\\,codex-lb-workload-1",
        "--set",
        "replicaCount=3",
    )

    assert 'missing pod name(s) "codex-lb-workload-2"' in stderr


def test_static_ring_with_wrong_pod_names_fails_at_render_time() -> None:
    """Right entry count, wrong values: the pods would still crashloop at startup."""
    stderr = _helm_template_error(
        "--set",
        "postgresql.auth.password=test-password",
        "--set-string",
        "config.sessionBridgeInstanceRing=codex-lb-0\\,codex-lb-1",
        "--set",
        "replicaCount=2",
    )

    assert 'missing pod name(s) "codex-lb-workload-0,codex-lb-workload-1"' in stderr
    assert 'must list exactly "codex-lb-workload-0,codex-lb-workload-1"' in stderr


def test_static_ring_with_fqdn_entries_fails_at_render_time() -> None:
    fqdn_ring = "\\,".join(
        f"codex-lb-workload-{ordinal}.codex-lb-bridge.default.svc.cluster.local" for ordinal in range(2)
    )
    stderr = _helm_template_error(
        "--set",
        "postgresql.auth.password=test-password",
        "--set-string",
        f"config.sessionBridgeInstanceRing={fqdn_ring}",
        "--set",
        "replicaCount=2",
    )

    assert "missing pod name(s)" in stderr


def test_static_ring_with_extra_unknown_entry_fails_at_render_time() -> None:
    stderr = _helm_template_error(
        "--set",
        "postgresql.auth.password=test-password",
        "--set-string",
        "config.sessionBridgeInstanceRing=codex-lb-workload-0\\,codex-lb-workload-1\\,codex-lb-workload-9",
        "--set",
        "replicaCount=2",
    )

    assert 'entry(ies) "codex-lb-workload-9" do not match any StatefulSet pod name' in stderr


def test_static_ring_covering_every_replica_renders() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.auth.password=test-password",
        "--set-string",
        "config.sessionBridgeInstanceRing=codex-lb-workload-0\\,codex-lb-workload-1",
        "--set",
        "replicaCount=2",
        "--show-only",
        "templates/deployment.yaml",
    )

    assert "kind: StatefulSet" in rendered


def _readme_config_examples() -> list[dict]:
    readme = _CHART_README.read_text()
    examples = []
    for fence in re.findall(r"```yaml\n(.*?)```", readme, re.DOTALL):
        parsed = yaml.safe_load(fence)
        if isinstance(parsed, dict) and isinstance(parsed.get("config"), dict):
            examples.append(parsed["config"])
    return examples


def _clear_pod_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("POD_NAME", "POD_NAMESPACE", "POD_IP", "HOSTNAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING", raising=False)
    monkeypatch.delenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL", raising=False)


def test_readme_advertise_base_url_example_passes_settings_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    examples = [
        config["sessionBridgeAdvertiseBaseUrl"]
        for config in _readme_config_examples()
        if "sessionBridgeAdvertiseBaseUrl" in config
    ]
    assert examples, "README no longer documents a sessionBridgeAdvertiseBaseUrl example"

    pod_name = "codex-lb-workload-0"
    _clear_pod_identity_env(monkeypatch)
    monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", pod_name)

    for example in examples:
        # The chart injects the value through the container env list after POD_NAME,
        # so the kubelet expands $(POD_NAME) per pod before the app reads it.
        assert "$(POD_NAME)" in example, f"README advertise example is not per-pod: {example}"
        expanded = example.replace("$(POD_NAME)", pod_name)
        monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL", expanded)

        settings = Settings()

        assert settings.http_responses_session_bridge_advertise_base_url == expanded.rstrip("/")


def test_readme_manual_ring_example_passes_settings_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    examples = [
        config["sessionBridgeInstanceRing"]
        for config in _readme_config_examples()
        if "sessionBridgeInstanceRing" in config
    ]
    assert examples, "README no longer documents a sessionBridgeInstanceRing example"

    _clear_pod_identity_env(monkeypatch)

    for example in examples:
        entries = [entry.strip() for entry in example.split(",") if entry.strip()]
        assert entries, f"README ring example is empty: {example}"
        for entry in entries:
            # Instance ids are bare $(POD_NAME) values; FQDN entries never match them.
            assert "." not in entry, f"README ring example uses a non-pod-name entry: {entry}"

        monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID", entries[0])
        monkeypatch.setenv("CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING", example)

        settings = Settings()

        assert settings.http_responses_session_bridge_instance_ring == entries


def test_kind_smoke_external_db_mode_exercises_two_replica_bridge_ring() -> None:
    script = _SMOKE_SCRIPT.read_text()

    assert "--set replicaCount=2" in script
    assert "--set replicaCount=1" not in script
    assert 'assert_bridge_ring "${release}" "${namespace}" 2' in script
    assert "/health/ready" in script
    assert 'ring.get("ring_size") == expected' in script
    assert 'ring.get("is_member") is True' in script
    assert "--for=condition=Ready" in script
    # kubectl exec only forwards the heredoc probe program when -i/--stdin is set;
    # without it `python -` sees EOF and exits 0 without running any assertion.
    assert 'exec -i "${workload}-0"' in script
    # The probe result must be checked so a silent no-op cannot pass smoke again.
    assert 'if [[ "${probe_output}" != "bridge ring ok:"* ]]; then' in script


def _env_example_active_assignments() -> dict[str, str]:
    assignments: dict[str, str] = {}
    for raw_line in _ENV_EXAMPLE.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        assignments[key.strip()] = value.strip()
    return assignments


def test_env_example_does_not_force_leader_election_off() -> None:
    """A fresh copy-the-sample deployment must inherit the hardened default (enabled).

    The runtime default for ``leader_election_enabled`` is True, so the sample env
    must not export ``CODEX_LB_LEADER_ELECTION_ENABLED=false`` as an active line;
    otherwise multi-replica/multi-worker installs that copy .env.example silently
    run every singleton scheduler instead of gating on the lease.
    """
    assignments = _env_example_active_assignments()

    assert assignments.get("CODEX_LB_LEADER_ELECTION_ENABLED") != "false"

    # A default-loaded Settings (with the sample's active assignments applied)
    # keeps leader election enabled.
    assert Settings().leader_election_enabled is True

    # The opt-out is still documented as a commented single-instance escape hatch.
    text = _ENV_EXAMPLE.read_text()
    assert "# CODEX_LB_LEADER_ELECTION_ENABLED=false" in text


def test_helm_configmap_enables_leader_election_by_default() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.auth.password=test-password",
        "--show-only",
        "templates/configmap.yaml",
    )
    (configmap,) = _helm_documents(rendered)

    assert configmap["data"]["CODEX_LB_LEADER_ELECTION_ENABLED"] == "true"


def test_compose_files_declare_single_replica_topology() -> None:
    for compose_path in (_REPO_ROOT / "docker-compose.yml", _REPO_ROOT / "docker-compose.prod.yml"):
        content = compose_path.read_text()
        assert "SINGLE-REPLICA topology" in content, compose_path.name
        assert "--scale server=N" in content, compose_path.name
        assert "deploy/helm/codex-lb" in content, compose_path.name
