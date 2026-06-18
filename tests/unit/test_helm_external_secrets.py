from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHART_DIR = _REPO_ROOT / "deploy" / "helm" / "codex-lb"
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
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering tests")
    _ensure_chart_dependencies()
    completed = subprocess.run(
        ["helm", "template", "codex-lb", str(_CHART_DIR), *args],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _helm_documents(rendered: str) -> list[dict]:
    return [document for document in yaml.safe_load_all(rendered) if document]


def _smoke_install_command(script: str, log_message: str) -> str:
    lines = script.splitlines()
    start = next(index for index, line in enumerate(lines) if f'log_step "{log_message}"' in line)
    command_start = next(
        index for index in range(start + 1, len(lines)) if re.match(r"\s*helm upgrade --install\b", lines[index])
    )
    command_lines = []
    for line in lines[command_start:]:
        command_lines.append(line.strip())
        if not line.rstrip().endswith("\\"):
            break
    return " ".join(command_lines).replace("\\", " ")


def _command_sets_value(command: str, value: str) -> bool:
    return re.search(rf"(?:^|\s)--set(?:\s+|=){re.escape(value)}(?:\s|$)", command) is not None


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


def test_upgrade_renders_legacy_deployment_cleanup_hook_for_statefulset_migration() -> None:
    rendered = _helm_template(
        "--is-upgrade",
        "--show-only",
        "templates/legacy-deployment-cleanup-hook.yaml",
    )

    assert "kind: Job" in rendered
    assert '"helm.sh/hook": post-upgrade' in rendered
    assert "LEGACY_DEPLOYMENT_NAME" in rendered
    assert "PUBLIC_SERVICE_NAME" in rendered
    assert "STATEFULSET_NAME" in rendered
    assert "STATEFULSET_MIN_REPLICAS" in rendered
    assert 'desired = int(spec.get("replicas") or int(os.environ.get("STATEFULSET_MIN_REPLICAS", "1")))' in rendered
    assert "desired = max(desired, min(legacy_ready, max_replicas))" not in rendered
    assert 'codex-lb.soju.dev/traffic": "workload"' in rendered
    assert "if ready >= desired:" in rendered


def test_upgrade_renders_legacy_deployment_prepare_hook() -> None:
    rendered = _helm_template(
        "--is-upgrade",
        "--show-only",
        "templates/legacy-deployment-prepare-hook.yaml",
    )

    assert "kind: Job" in rendered
    assert '"helm.sh/hook": pre-upgrade' in rendered
    assert 'codex-lb.soju.dev/traffic": "legacy"' in rendered
    assert "raise SystemExit(0)" in rendered


def test_public_service_can_render_legacy_selector_for_cutover() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/service.yaml",
        "--set",
        "migration.serviceSelectorMode=legacy",
    )

    assert "codex-lb.soju.dev/traffic: legacy" in rendered


def test_public_service_can_render_workload_selector_after_cutover() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/service.yaml",
        "--set",
        "migration.serviceSelectorMode=workload",
    )

    assert "codex-lb.soju.dev/traffic: workload" in rendered


def test_public_service_auto_mode_renders_legacy_selector_on_upgrade_without_lookup() -> None:
    rendered = _helm_template(
        "--is-upgrade",
        "--show-only",
        "templates/service.yaml",
        "--set",
        "migration.serviceSelectorMode=auto",
    )

    assert "codex-lb.soju.dev/traffic: legacy" in rendered


def test_statefulset_translates_legacy_recreate_strategy_to_rolling_update() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "updateStrategy.type=Recreate",
    )

    assert "kind: StatefulSet" in rendered
    assert "updateStrategy:" in rendered
    assert "type: RollingUpdate" in rendered
    assert "type: Recreate" not in rendered


def test_public_service_auto_mode_renders_workload_selector_on_install() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/service.yaml",
        "--set",
        "migration.serviceSelectorMode=auto",
    )

    assert "codex-lb.soju.dev/traffic: workload" in rendered


def test_legacy_cleanup_hook_includes_image_pull_secrets() -> None:
    rendered = _helm_template(
        "--is-upgrade",
        "--show-only",
        "templates/legacy-deployment-cleanup-hook.yaml",
        "--set",
        "image.pullSecrets[0]=private-registry",
    )

    assert "imagePullSecrets:" in rendered
    assert "name: private-registry" in rendered


def test_chart_managed_secret_uses_post_install_hook_path() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.enabled=false",
        "--set",
        "externalSecrets.enabled=false",
        "--set",
        "externalDatabase.url=postgresql+asyncpg://user:pass@db.example.com:5432/codexlb",
        "--set",
        "migration.enabled=true",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "false"' in rendered
    assert '"helm.sh/hook": "post-install,pre-upgrade"' in rendered
    assert "serviceAccountName: default" in rendered


def test_direct_external_database_install_uses_post_install_hook_path() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.enabled=false",
        "--set",
        "externalDatabase.url=postgresql+asyncpg://user:pass@db.example.com:5432/codexlb",
        "--set",
        "migration.enabled=true",
    )

    assert '"helm.sh/hook": "post-install,pre-upgrade"' in rendered
    assert "serviceAccountName: default" in rendered


def test_bundled_mode_overlay_enables_startup_migration_and_skips_schema_gate() -> None:
    rendered = _helm_template(
        "-f",
        str(_CHART_DIR / "values-bundled.yaml"),
        "--set",
        "postgresql.auth.password=local-password",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "true"' in rendered
    assert "name: wait-for-schema-head" not in rendered
    assert "name: wait-for-database" in rendered
    assert '"helm.sh/hook": "pre-upgrade"' in rendered


def test_existing_secret_install_keeps_pre_install_hook_path() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.enabled=false",
        "--set",
        "auth.existingSecret=codex-lb-secrets",
        "--set",
        "externalDatabase.url=postgresql+asyncpg://user:pass@db.example.com:5432/codexlb",
        "--set",
        "migration.enabled=true",
    )

    assert 'CODEX_LB_DATABASE_MIGRATE_ON_STARTUP: "false"' in rendered
    assert '"helm.sh/hook": "pre-install,pre-upgrade"' in rendered
    assert "serviceAccountName: default" in rendered


def test_external_database_existing_secret_install_keeps_pre_install_hook_path() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.enabled=false",
        "--set",
        "externalDatabase.existingSecret=external-db-secret",
        "--set",
        "auth.existingSecret=codex-lb-secrets",
        "--set",
        "migration.enabled=true",
    )

    assert '"helm.sh/hook": "pre-install,pre-upgrade"' in rendered


def test_external_db_mode_overlay_renders_schema_gate_init_container() -> None:
    rendered = _helm_template(
        "-f",
        str(_CHART_DIR / "values-external-db.yaml"),
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "externalDatabase.url=postgresql+asyncpg://user:pass@db.example.com:5432/codexlb",
    )

    assert "name: wait-for-schema-head" in rendered
    assert "wait-for-head" in rendered


def test_deployment_prestop_starts_and_polls_local_drain() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "service.port=3456",
    )

    assert "http://127.0.0.1:3456" in rendered
    assert "/internal/drain/start" in rendered
    assert "/internal/drain/status" in rendered
    assert "deadline = time.monotonic() + 15" in rendered
    assert "break" not in rendered


def test_deployment_uses_service_port_for_container_and_probes() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "service.port=3456",
    )

    assert '- "3456"' in rendered
    assert "containerPort: 3456" in rendered


def test_deployment_anti_affinity_targets_workload_lane_only() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "affinity.podAntiAffinity=hard",
    )

    assert "codex-lb.soju.dev/traffic: workload" in rendered
    assert "codex-lb.soju.dev/traffic: legacy" not in rendered


def test_deployment_sets_encryption_key_file_env_by_default() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
    )

    assert "CODEX_LB_ENCRYPTION_KEY_FILE" in rendered
    assert "/var/lib/codex-lb/encryption.key" in rendered


def test_ingress_renders_dedicated_responses_ingress_with_session_hash() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/ingress.yaml",
        "--set",
        "ingress.enabled=true",
        "--set",
        "ingress.ingressClassName=nginx",
        "--set",
        "ingress.nginx.enabled=true",
        "--set-string",
        "ingress.hosts[0].host=codex-lb.localtest.me",
    )

    assert rendered.count("kind: Ingress") == 2
    assert "name: codex-lb-responses" in rendered
    assert "nginx.ingress.kubernetes.io/upstream-hash-by: $codex_responses_hash_key" in rendered
    assert "nginx.ingress.kubernetes.io/configuration-snippet:" in rendered
    assert 'set $codex_responses_hash_key "$http_authorization:$request_id";' in rendered
    assert "set $codex_responses_hash_key $http_x_codex_session_id;" in rendered
    assert "nginx.ingress.kubernetes.io/upstream-hash-by: $http_authorization" in rendered
    assert "nginx.ingress.kubernetes.io/proxy-next-upstream: error timeout http_502 http_503 http_504" in rendered
    assert "invalid_header" in rendered
    assert 'nginx.ingress.kubernetes.io/proxy-next-upstream-tries: "2"' in rendered
    assert "path: /v1/responses" in rendered
    assert "path: /backend-api/codex/responses" in rendered


def test_bundled_kind_smoke_preserves_primary_ingress_paths() -> None:
    script = (_REPO_ROOT / "scripts" / "helm-kind-smoke.sh").read_text()

    assert "--set-string 'ingress.hosts[0].host=codex-lb.localtest.me'" in script
    assert "--set-string 'ingress.hosts[0].paths[0].path=/'" in script
    assert "--set-string 'ingress.hosts[0].paths[0].pathType=Prefix'" in script
    assert 'run_bundled_migration "${release}" "${namespace}"' not in script
    assert "config.databaseMigrateOnStartup=false" not in script
    assert "--wait \\" in script


def test_helm_test_pod_uses_configurable_default_image() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/tests/test-connection.yaml",
    )

    assert "image: docker.io/library/busybox:1.37" in rendered
    assert "imagePullPolicy: IfNotPresent" in rendered


def test_helm_test_pod_image_can_be_overridden() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/tests/test-connection.yaml",
        "--set",
        "test.image.registry=ghcr.io",
        "--set",
        "test.image.repository=soju06/codex-lb",
        "--set",
        "test.image.tag=ci",
        "--set",
        "test.image.pullPolicy=Never",
    )

    assert "image: ghcr.io/soju06/codex-lb:ci" in rendered
    assert "imagePullPolicy: Never" in rendered
    assert "docker.io/library/busybox:1.37" not in rendered

    (test_pod,) = _helm_documents(rendered)
    (container,) = test_pod["spec"]["containers"]
    assert container["command"][:2] == ["sh", "-c"]
    script = container["command"][2]
    assert "if command -v python >/dev/null 2>&1; then" in script
    assert "python - <<'PY'" in script
    assert "import urllib.request" in script
    assert "urllib.request.urlopen(url, timeout=10)" in script
    assert "else" in script
    assert "wget --spider --timeout=10 http://codex-lb:2455/health || exit 1" in script
    assert "wget -qO- --timeout=10 http://codex-lb:2455/health/ready || exit 1" in script


def test_kind_smoke_overrides_helm_test_image_and_external_db_replicas() -> None:
    script = (_REPO_ROOT / "scripts" / "helm-kind-smoke.sh").read_text()
    bundled_install = _smoke_install_command(script, "installing bundled release ${release}")
    external_db_install = _smoke_install_command(script, "installing external DB release ${release}")

    assert '--set test.image.registry="${IMAGE_REGISTRY}"' in script
    assert '--set test.image.repository="${IMAGE_REPOSITORY}"' in script
    assert '--set test.image.tag="${IMAGE_TAG}"' in script
    assert "--set test.image.pullPolicy=IfNotPresent" in script
    assert _command_sets_value(external_db_install, "replicaCount=1")
    assert not _command_sets_value(bundled_install, "replicaCount=1")


def test_kind_smoke_logs_timestamped_major_steps() -> None:
    script = (_REPO_ROOT / "scripts" / "helm-kind-smoke.sh").read_text()

    assert 'date -u +"%Y-%m-%dT%H:%M:%SZ"' in script
    assert "log_step" in script
    assert 'log_step "building Helm dependencies"' in script
    assert 'log_step "installing bundled release ${release}"' in script
    assert 'log_step "installing external PostgreSQL release ${db_release}"' in script
    assert 'log_step "running helm test for ${release} in ${namespace}"' in script


def test_kind_smoke_bounds_helm_test_wait() -> None:
    script = (_REPO_ROOT / "scripts" / "helm-kind-smoke.sh").read_text()

    assert 'HELM_TEST_TIMEOUT="${HELM_TEST_TIMEOUT:-60s}"' in script
    assert (
        'helm test "${release}" --namespace "${namespace}" --kube-context "${KUBE_CONTEXT}" '
        '--timeout "${HELM_TEST_TIMEOUT}"'
    ) in script


def test_auto_advertise_bridge_url_uses_service_port() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "service.port=3456",
    )

    assert "svc.cluster.local:3456" in rendered


def test_migration_job_image_does_not_duplicate_registry_prefix() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/hooks/migration-job.yaml",
        "--set",
        "image.registry=ghcr.io",
        "--set",
        "image.repository=soju06/codex-lb",
        "--set",
        "image.tag=local-test",
    )

    assert "ghcr.io/ghcr.io/" not in rendered
    assert "ghcr.io/soju06/codex-lb:local-test" in rendered


def test_external_secrets_mode_overlay_renders_schema_gate_init_container() -> None:
    rendered = _helm_template(
        "-f",
        str(_CHART_DIR / "values-external-secrets.yaml"),
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "externalSecrets.secretStoreRef.name=test-store",
    )

    assert "name: wait-for-schema-head" in rendered
    assert "wait-for-head" in rendered


def test_schema_gate_can_be_disabled() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        "postgresql.auth.password=local-password",
        "--set",
        "migration.schemaGate.enabled=false",
    )

    assert "name: wait-for-schema-head" not in rendered


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


def test_statefulset_workload_name_leaves_room_for_pod_ordinal() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/deployment.yaml",
        "--set",
        f"fullnameOverride={'a' * 63}",
    )

    match = re.search(r"kind: StatefulSet.*?metadata:\n  name: ([^\n]+)", rendered, re.DOTALL)
    assert match is not None
    workload_name = match.group(1).strip()
    assert len(workload_name) <= 52


def test_headless_service_publishes_not_ready_addresses_for_bridge_dns() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/service-headless.yaml",
    )

    assert "publishNotReadyAddresses: true" in rendered


def test_external_database_existing_secret_is_used_for_database_url_env() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.enabled=false",
        "--set",
        "externalDatabase.existingSecret=external-db-secret",
    )

    assert re.search(
        r"name: CODEX_LB_DATABASE_URL\s+valueFrom:\s+secretKeyRef:\s+name: external-db-secret\s+key: database-url",
        rendered,
        re.S,
    )


def test_chart_managed_secret_omits_database_url_when_external_database_secret_is_used() -> None:
    rendered = _helm_template(
        "--show-only",
        "templates/secret.yaml",
        "--set",
        "postgresql.enabled=false",
        "--set",
        "externalDatabase.existingSecret=external-db-secret",
    )

    assert "database-url:" not in rendered
    assert "encryption-key:" in rendered


def test_external_database_url_is_rendered_into_chart_managed_secret_when_postgresql_is_disabled() -> None:
    rendered = _helm_template(
        "--set",
        "postgresql.enabled=false",
        "--set",
        "externalDatabase.url=postgresql+asyncpg://user:pass@db.example.com:5432/codexlb",
    )

    assert 'database-url: "postgresql+asyncpg://user:pass@db.example.com:5432/codexlb"' in rendered


def test_network_policy_does_not_allow_http_ingress_from_all_namespaces_by_default() -> None:
    rendered = _helm_template(
        "-f",
        str(_CHART_DIR / "values-prod.yaml"),
        "--show-only",
        "templates/networkpolicy.yaml",
    )

    assert (
        "namespaceSelector: {}"
        not in rendered.split("# Allow internal bridge handoff", 1)[1].split(
            "# Allow metrics scraping from Prometheus",
            1,
        )[0]
    )


def test_network_policy_allows_internal_bridge_handoff_egress_between_pods() -> None:
    rendered = _helm_template(
        "-f",
        str(_CHART_DIR / "values-prod.yaml"),
        "--show-only",
        "templates/networkpolicy.yaml",
        "--set",
        "service.port=3456",
    )

    assert "# Allow pod-to-pod bridge owner handoff egress" in rendered
    assert "port: 3456" in rendered.split("# Allow pod-to-pod bridge owner handoff egress", 1)[1]
