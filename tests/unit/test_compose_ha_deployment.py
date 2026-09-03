from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "deploy/compose/docker-compose.ha.yml"
HAPROXY_PATH = REPO_ROOT / "deploy/compose/haproxy.cfg"
DEPLOY_SCRIPT = REPO_ROOT / "scripts/deploy-compose-ha.sh"


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_ha_compose_keeps_application_slots_private() -> None:
    services = _compose()["services"]

    assert services["haproxy"]["ports"] == ["2455:2455"]
    assert services["server-blue"]["expose"] == ["2455"]
    assert services["server-green"]["expose"] == ["2455"]
    assert "ports" not in services["server-blue"]
    assert "ports" not in services["server-green"]
    assert services["haproxy"]["image"].startswith("haproxy:3.2-alpine@sha256:")
    assert len(services["haproxy"]["image"].rsplit("@sha256:", 1)[1]) == 64


def test_ha_slots_have_stable_unique_identity_and_shared_state() -> None:
    services = _compose()["services"]
    blue = services["server-blue"]
    green = services["server-green"]

    assert blue["environment"] == {
        "FORWARDED_ALLOW_IPS": "172.31.245.254",
        "CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS": "true",
        "CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS": "172.31.245.254/32",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID": "server-blue",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING": "server-blue,server-green",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL": "http://server-blue:2455",
    }
    assert green["environment"] == {
        "FORWARDED_ALLOW_IPS": "172.31.245.254",
        "CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS": "true",
        "CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS": "172.31.245.254/32",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID": "server-green",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING": "server-blue,server-green",
        "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL": "http://server-green:2455",
    }
    assert blue["volumes"] == green["volumes"] == ["codex-lb-data:/var/lib/codex-lb"]
    assert blue["command"][:3] == ["python", "-m", "app.cli"]
    assert green["command"][:3] == ["python", "-m", "app.cli"]
    assert blue["healthcheck"] == green["healthcheck"]
    assert blue["stop_grace_period"] == green["stop_grace_period"] == "75s"
    assert services["haproxy"]["networks"]["ha-edge"]["ipv4_address"] == "172.31.245.254"
    assert blue["networks"] == green["networks"] == {"ha-edge": {}, "data": {}}


def test_haproxy_config_supports_readiness_streams_and_private_runtime_control() -> None:
    config = HAPROXY_PATH.read_text(encoding="utf-8")

    assert "stats socket ipv4@127.0.0.1:9999 level admin" in config
    assert "server-state-file /var/lib/haproxy/server-state" in config
    assert "load-server-state-from-file global" in config
    assert "http-check send meth GET uri /health/ready" in config
    assert "timeout tunnel 65m" in config
    assert "option http-keep-alive" in config
    assert "option forwardfor" in config
    assert "http-request del-header X-Forwarded-For" in config
    assert "server blue server-blue:2455 check" in config
    assert "server green server-green:2455 weight 0 check" in config


def _write_fake_docker(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"

if [[ -n "${FAKE_FAIL_MATCH:-}" && " $* " == *"$FAKE_FAIL_MATCH"* ]]; then
    exit 1
fi
if [[ "${1:-}" == network ]]; then
    exit 0
fi
if [[ "${1:-}" == inspect ]]; then
    printf 'healthy\\n'
    exit 0
fi
if [[ " $* " != *" compose "* ]]; then
    exit 0
fi
if [[ " $* " == *" config --quiet "* ]]; then
    exit 0
fi
if [[ " $* " == *" ps --quiet server-blue "* ]]; then
    printf 'blue-container\\n'
    exit 0
fi
if [[ " $* " == *" ps --quiet server-green "* ]]; then
    printf 'green-container\\n'
    exit 0
fi
if [[ " $* " == *"docker-compose.prod.yml"* && " $* " == *" ps --quiet server "* ]]; then
    exit 0
fi
if [[ " $* " == *" exec --no-TTY haproxy "* && " $* " == *" wget "* ]]; then
    exit 0
fi
if [[ " $* " == *" exec --no-TTY haproxy sh -c "* ]]; then
    payload="$(cat)"
    printf 'runtime:%s\\n' "$payload" >>"$FAKE_DOCKER_LOG"
    if [[ -n "${FAKE_FAIL_MATCH:-}" && "$payload" == *"$FAKE_FAIL_MATCH"* ]]; then
        exit 1
    fi
    if [[ "$payload" == *"show stat"* ]]; then
        printf '# pxname,svname,qcur,qmax,scur,smax,slim,stot,bin,bout,dreq,dresp,ereq,econ,'
        printf 'eresp,wretr,wredis,status\\n'
        printf 'codex_lb_slots,blue,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,UP,100\\n'
        printf 'codex_lb_slots,green,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,DRAIN,0\\n'
    elif [[ "$payload" == *"show servers state"* ]]; then
        printf '1\\n# be_id be_name srv_id srv_name srv_addr srv_op_state srv_admin_state\\n'
    fi
    exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _workflow_repo(tmp_path: Path, env_contents: str) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "deploy/compose").mkdir(parents=True)
    shutil.copy2(DEPLOY_SCRIPT, root / "scripts/deploy-compose-ha.sh")
    shutil.copy2(COMPOSE_PATH, root / "deploy/compose/docker-compose.ha.yml")
    shutil.copy2(HAPROXY_PATH, root / "deploy/compose/haproxy.cfg")
    (root / "docker-compose.prod.yml").write_text("services: {}\n", encoding="utf-8")
    (root / ".env.local").write_text(env_contents, encoding="utf-8")
    fake_docker = tmp_path / "fake-docker"
    _write_fake_docker(fake_docker)
    return root, fake_docker


def _run_workflow(
    root: Path,
    fake_docker: Path,
    command: str,
    *,
    fail_match: str | None = None,
) -> subprocess.CompletedProcess[str]:
    log_path = root / "docker.log"
    env = {
        **os.environ,
        "_CODEX_LB_HA_DOCKER_BIN": str(fake_docker),
        "_CODEX_LB_HA_READY_ATTEMPTS": "1",
        "_CODEX_LB_HA_PUBLIC_READY_ATTEMPTS": "1",
        "FAKE_DOCKER_LOG": str(log_path),
    }
    if fail_match is not None:
        env["FAKE_FAIL_MATCH"] = fail_match
    return subprocess.run(
        [str(root / "scripts/deploy-compose-ha.sh"), command, "1"],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "unsafe_env,expected_error",
    [
        ("CODEX_LB_DATABASE_URL=sqlite+aiosqlite:///store.db\n", "shared PostgreSQL"),
        (
            "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\nCODEX_LB_LEADER_ELECTION_ENABLED=false\n",
            "must remain enabled",
        ),
    ],
)
def test_deployment_rejects_unsafe_overlap_before_container_mutation(
    tmp_path: Path,
    unsafe_env: str,
    expected_error: str,
) -> None:
    root, fake_docker = _workflow_repo(tmp_path, unsafe_env)

    result = _run_workflow(root, fake_docker, "bootstrap")

    assert result.returncode != 0
    assert expected_error in result.stderr
    if (root / "docker.log").exists():
        assert " up " not in (root / "docker.log").read_text(encoding="utf-8")
    assert not (root / ".codex-lb-ha/active-slot").exists()


def test_fake_docker_workflow_bootstraps_then_switches_blue_to_green(tmp_path: Path) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n"
        "CODEX_LB_ENCRYPTION_KEY_FILE=/var/lib/codex-lb/encryption.key\n",
    )

    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    deploy = _run_workflow(root, fake_docker, "deploy")

    assert bootstrap.returncode == 0, bootstrap.stderr
    assert deploy.returncode == 0, deploy.stderr
    state_dir = root / ".codex-lb-ha"
    assert (state_dir / "active-slot").read_text(encoding="utf-8").strip() == "green"
    assert not (state_dir / "draining-slot").exists()
    assert (state_dir / "server-state").exists()

    log = (root / "docker.log").read_text(encoding="utf-8")
    candidate_start = log.index("up --detach --build --no-deps server-green")
    candidate_ready = log.index("wget -q -T 3 -O /dev/null http://server-green:2455/health/ready")
    cutover = log.index("set server codex_lb_slots/green weight 1")
    predecessor_stop = log.rindex("stop server-blue")
    assert candidate_start < candidate_ready < cutover < predecessor_stop
    assert "set server codex_lb_slots/blue weight 0" in log
    assert "weight 100%" not in log


@pytest.mark.parametrize(
    "failed_stage,expected_error",
    [
        ("up --detach --build --no-deps server-green", "failed to build or start"),
        ("http://server-green:2455/health/ready", "did not reach strict readiness"),
        ("set server codex_lb_slots/green weight 1", "rejected the cutover"),
        ("http://127.0.0.1:2455/health/ready", "public verification failed"),
    ],
)
def test_failed_deployment_stage_keeps_blue_active(
    tmp_path: Path,
    failed_stage: str,
    expected_error: str,
) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    log_path = root / "docker.log"
    log_path.write_text("", encoding="utf-8")
    deploy = _run_workflow(root, fake_docker, "deploy", fail_match=failed_stage)

    assert deploy.returncode != 0
    assert expected_error in deploy.stderr
    assert (root / ".codex-lb-ha/active-slot").read_text(encoding="utf-8").strip() == "blue"
    log = log_path.read_text(encoding="utf-8")
    assert "stop server-blue" not in log


def test_fake_docker_workflow_rolls_back_during_drain(tmp_path: Path) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    state_dir = root / ".codex-lb-ha"
    (state_dir / "active-slot").write_text("green\n", encoding="utf-8")
    (state_dir / "draining-slot").write_text("blue\n", encoding="utf-8")
    log_path = root / "docker.log"
    log_path.write_text("", encoding="utf-8")

    rollback = _run_workflow(root, fake_docker, "rollback")

    assert rollback.returncode == 0, rollback.stderr
    assert (state_dir / "active-slot").read_text(encoding="utf-8").strip() == "blue"
    assert not (state_dir / "draining-slot").exists()
    log = log_path.read_text(encoding="utf-8")
    ready = log.index("set server codex_lb_slots/blue weight 1")
    drain = log.index("set server codex_lb_slots/green weight 0")
    stop = log.index("stop server-green")
    assert ready < drain < stop


def test_concurrent_deployment_is_rejected_before_mutation(tmp_path: Path) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    log_path = root / "docker.log"
    log_path.write_text("", encoding="utf-8")
    with (root / ".codex-lb-ha/deploy.lock").open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deploy = _run_workflow(root, fake_docker, "deploy")

    assert deploy.returncode != 0
    assert "another HA deployment command" in deploy.stderr
    assert log_path.read_text(encoding="utf-8") == ""


def test_deployment_script_help_is_available_without_docker() -> None:
    result = subprocess.run(
        [str(DEPLOY_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "bootstrap" in result.stdout
    assert "rollback" in result.stdout
