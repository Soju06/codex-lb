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


def test_ha_compose_keeps_application_backends_private() -> None:
    services = _compose()["services"]

    assert services["haproxy"]["ports"] == ["2455:2455"]
    for service_name in ("server-blue", "server-green", "server-amber", "server-surge"):
        assert services[service_name]["expose"] == ["2455"]
        assert "ports" not in services[service_name]
    assert services["haproxy"]["image"].startswith("haproxy:3.2-alpine@sha256:")
    assert len(services["haproxy"]["image"].rsplit("@sha256:", 1)[1]) == 64


def test_ha_backends_have_stable_unique_identity_and_shared_state() -> None:
    services = _compose()["services"]
    backends = {name: services[f"server-{name}"] for name in ("blue", "green", "amber", "surge")}
    expected_ring = "server-blue,server-green,server-amber,server-surge"

    for name, service in backends.items():
        assert service["environment"] == {
            "CODEX_LB_DATABASE_POOL_SIZE": "8",
            "CODEX_LB_DATABASE_MAX_OVERFLOW": "2",
            "CODEX_LB_NATIVE_WEBSOCKET_BUFFER_MAX_BYTES": "1073741824",
            "FORWARDED_ALLOW_IPS": "172.31.245.254",
            "CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS": "true",
            "CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS": "172.31.245.254/32",
            "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID": f"server-{name}",
            "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING": expected_ring,
            "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL": (f"http://server-{name}:2455"),
        }
        assert service["volumes"] == ["codex-lb-data:/var/lib/codex-lb"]
        assert service["command"][:3] == ["python", "-m", "app.cli"]
        assert service["stop_grace_period"] == "75s"
        assert service["networks"] == {"ha-edge": {}, "data": {}}

    assert backends["blue"]["healthcheck"] == backends["green"]["healthcheck"]
    assert backends["green"]["healthcheck"] == backends["surge"]["healthcheck"]
    assert services["haproxy"]["networks"]["ha-edge"]["ipv4_address"] == ("172.31.245.254")


def test_haproxy_config_supports_active_active_plus_surge() -> None:
    config = HAPROXY_PATH.read_text(encoding="utf-8")

    assert "stats socket ipv4@127.0.0.1:9999 level admin" in config
    assert "server-state-file /var/lib/haproxy/server-state" in config
    assert "load-server-state-from-file global" in config
    assert "http-check send meth GET uri /health/ready" in config
    assert "timeout tunnel 65m" in config
    assert "option http-keep-alive" in config
    assert "option forwardfor" in config
    assert "http-request del-header X-Forwarded-For" in config
    assert "server blue server-blue:2455 id 1 check" in config
    assert "server green server-green:2455 id 2 check" in config
    assert "server green server-green:2455 id 2 weight 0" not in config
    assert "server surge server-surge:2455 id 3 weight 0 check" in config
    assert "server amber server-amber:2455 id 4 check" in config
    assert "balance leastconn" in config


def test_ha_resource_budget_includes_two_pools_and_surge() -> None:
    backends = [service for name, service in _compose()["services"].items() if name.startswith("server-")]
    assert len(backends) == 4
    assert all(service["deploy"]["resources"]["limits"]["memory"] == "3G" for service in backends)
    assert (
        sum(
            2
            * (
                int(service["environment"]["CODEX_LB_DATABASE_POOL_SIZE"])
                + int(service["environment"]["CODEX_LB_DATABASE_MAX_OVERFLOW"])
            )
            for service in backends
        )
        == 80
    )


def _write_fake_docker(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
mkdir -p "$FAKE_DOCKER_STATE"

if [[ -n "${FAKE_FAIL_MATCH:-}" && " $* " == *"$FAKE_FAIL_MATCH"* ]]; then
    exit 1
fi
if [[ "${1:-}" == network || "${1:-}" == volume ]]; then
    exit 0
fi
if [[ "${1:-}" == inspect ]]; then
    if [[ " $* " == *".NetworkSettings.Networks"* ]]; then
        printf '%s\\n' "${FAKE_SURGE_IP:-172.31.245.13}"
    else
        printf 'healthy\\n'
    fi
    exit 0
fi
if [[ "${1:-}" == kill ]]; then
    printf '102\\n' >"$FAKE_DOCKER_STATE/worker.pid"
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
if [[ " $* " == *" ps --quiet server-amber "* ]]; then
    printf 'amber-container\\n'
    exit 0
fi
if [[ " $* " == *" ps --quiet server-surge "* ]]; then
    printf 'surge-container\\n'
    exit 0
fi
if [[ " $* " == *" ps --quiet haproxy "* ]]; then
    printf 'haproxy-container\\n'
    exit 0
fi
if [[ " $* " == *"docker-compose.prod.yml"* && " $* " == *" ps --quiet server "* ]]; then
    exit 0
fi
if [[ " $* " == *" exec --no-TTY haproxy "* && " $* " == *" wget "* ]]; then
    exit 0
fi
if [[ " $* " == *" exec --no-TTY haproxy sha256sum "* ]]; then
    sha256sum deploy/compose/haproxy.cfg
    exit 0
fi
if [[ " $* " == *"/proc/1/task/1/children"* ]]; then
    printf '%s\\n' "${FAKE_WORKERS:-1}"
    exit 0
fi
if [[ " $* " == *" exec --no-TTY haproxy sh -c "* ]]; then
    payload="$(cat)"
    printf 'runtime:%s\\n' "$payload" >>"$FAKE_DOCKER_LOG"
    if [[ -n "${FAKE_FAIL_MATCH:-}" && "$payload" == *"$FAKE_FAIL_MATCH"* ]]; then
        exit 1
    fi

    for slot in blue green amber surge; do
        weight_file="$FAKE_DOCKER_STATE/$slot.weight"
        if [[ ! -f "$weight_file" ]]; then
            if [[ "$slot" == surge ]]; then
                printf '0\\n' >"$weight_file"
            else
                printf '100\\n' >"$weight_file"
            fi
        fi
    done

    if [[ "$payload" == *"show info"* ]]; then
        pid=101
        [[ ! -f "$FAKE_DOCKER_STATE/worker.pid" ]] || pid="$(<"$FAKE_DOCKER_STATE/worker.pid")"
        printf 'Pid: %s\\n' "$pid"
    elif [[ "$payload" == *"show stat"* ]]; then
        printf '# pxname,svname,qcur,qmax,scur,smax,slim,stot,bin,bout,dreq,dresp,ereq,econ,'
        printf 'eresp,wretr,wredis,status,weight\\n'
        for slot in blue green amber; do
            if [[ "$slot" == amber && "${FAKE_LEGACY_HAPROXY:-0}" == 1
                && ! -f "$FAKE_DOCKER_STATE/amber.registered" ]]; then
                continue
            fi
            weight="$(<"$FAKE_DOCKER_STATE/$slot.weight")"
            status=UP
            [[ "$weight" != 0 ]] || status=DRAIN
            printf 'codex_lb_slots,%s,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,%s,%s\\n' \
                "$slot" "$status" "$weight"
        done
        if [[ "${FAKE_LEGACY_HAPROXY:-0}" != 1 || -f "$FAKE_DOCKER_STATE/surge.registered" ]]; then
            weight="$(<"$FAKE_DOCKER_STATE/surge.weight")"
            status=UP
            [[ "$weight" != 0 ]] || status=DRAIN
            printf 'codex_lb_slots,surge,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,%s,%s\\n' \
                "$status" "$weight"
        fi
    elif [[ "$payload" == *"show servers state"* ]]; then
        printf '1\\n'
        printf '# be_id be_name srv_id srv_name srv_addr srv_op_state srv_admin_state srv_uweight '
        printf 'srv_iweight srv_time_since_last_change srv_check_status srv_check_result srv_check_health '
        printf 'srv_check_state srv_agent_state bk_f_forced_id srv_f_forced_id srv_fqdn srv_port\\n'
        for slot in blue green amber; do
            printf '3 codex_lb_slots 1 %s 172.31.245.12 2 0 1 1 0 15 3 3 6 0 0 0 server-%s 2455\\n' "$slot" "$slot"
        done
        if [[ -f "$FAKE_DOCKER_STATE/surge.registered" ]]; then
            address="$(<"$FAKE_DOCKER_STATE/surge.addr")"
            printf '3 codex_lb_slots 3 surge %s 2 0 1 0 0 15 3 3 6 0 0 0 - 2455\\n' "$address"
        elif [[ "${FAKE_LEGACY_HAPROXY:-0}" != 1 ]]; then
            printf '3 codex_lb_slots 3 surge 172.31.245.13 0 0 0 0 0 0 0 0 0 0 0 0 server-surge 2455\\n'
        fi
    elif [[ "$payload" =~ add[[:space:]]server[[:space:]]codex_lb_slots/amber ]]; then
        touch "$FAKE_DOCKER_STATE/amber.registered"
        printf 'New server registered.\\n'
    elif [[ "$payload" =~ add[[:space:]]server[[:space:]]codex_lb_slots/surge ]]; then
        touch "$FAKE_DOCKER_STATE/surge.registered"
        printf '%s\\n' "${FAKE_SURGE_IP:-172.31.245.13}" >"$FAKE_DOCKER_STATE/surge.addr"
        printf 'New server registered.\\n'
    else
        set_weight_re='set[[:space:]]server[[:space:]]codex_lb_slots/([a-z]+)[[:space:]]weight[[:space:]]([0-9]+)'
        set_addr_re='set[[:space:]]server[[:space:]]codex_lb_slots/surge[[:space:]]addr[[:space:]]([^[:space:]]+)'
        if [[ "$payload" =~ $set_weight_re ]]; then
            printf '%s\\n' "${BASH_REMATCH[2]}" >"$FAKE_DOCKER_STATE/${BASH_REMATCH[1]}.weight"
        elif [[ "$payload" =~ $set_addr_re ]]; then
            printf '%s\\n' "${BASH_REMATCH[1]}" >"$FAKE_DOCKER_STATE/surge.addr"
        fi
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
    legacy_haproxy: bool = False,
    surge_ip: str | None = None,
    workers: str = "1",
) -> subprocess.CompletedProcess[str]:
    log_path = root / "docker.log"
    env = {
        **os.environ,
        "_CODEX_LB_HA_DOCKER_BIN": str(fake_docker),
        "_CODEX_LB_HA_READY_ATTEMPTS": "1",
        "_CODEX_LB_HA_PUBLIC_READY_ATTEMPTS": "1",
        "FAKE_DOCKER_LOG": str(log_path),
        "FAKE_DOCKER_STATE": str(root / ".fake-docker-state"),
        "FAKE_WORKERS": workers,
    }
    if fail_match is not None:
        env["FAKE_FAIL_MATCH"] = fail_match
    if legacy_haproxy:
        env["FAKE_LEGACY_HAPROXY"] = "1"
    if surge_ip is not None:
        env["FAKE_SURGE_IP"] = surge_ip
    args = [str(root / "scripts/deploy-compose-ha.sh"), command]
    if command != "status":
        args.append("1")
    return subprocess.run(
        args,
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


def test_fake_docker_workflow_bootstraps_active_active_then_uses_surge(
    tmp_path: Path,
) -> None:
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
    assert (state_dir / "active-slot").read_text(encoding="utf-8").strip() == ("blue,green,amber")
    assert not (state_dir / "draining-slot").exists()
    assert (state_dir / "server-state").exists()

    log = (root / "docker.log").read_text(encoding="utf-8")
    surge_start = log.index("up --detach --build --no-deps server-surge")
    surge_active = log.index("set server codex_lb_slots/surge weight 1")
    blue_drain = log.index("set server codex_lb_slots/blue weight 0", surge_active)
    blue_stop = log.index("stop server-blue", blue_drain)
    blue_restart = log.index("up --detach --no-build --no-deps --force-recreate server-blue", blue_stop)
    green_drain = log.index("set server codex_lb_slots/green weight 0", blue_restart)
    surge_retire = log.rindex("set server codex_lb_slots/surge weight 0")
    surge_stop = log.rindex("stop server-surge")
    assert surge_start < surge_active < blue_drain < blue_stop < blue_restart < green_drain < surge_retire < surge_stop


def test_first_rollout_registers_surge_in_a_legacy_haproxy_process(
    tmp_path: Path,
) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    state_dir = root / ".codex-lb-ha"
    (state_dir / "active-slot").write_text("blue\n", encoding="utf-8")
    (root / ".fake-docker-state/green.weight").write_text("0\n", encoding="utf-8")
    log_path = root / "docker.log"
    log_path.write_text("", encoding="utf-8")
    deploy = _run_workflow(root, fake_docker, "deploy", legacy_haproxy=True)

    assert deploy.returncode == 0, deploy.stderr
    assert (state_dir / "active-slot").read_text(encoding="utf-8").strip() == ("blue,green,amber")
    log = log_path.read_text(encoding="utf-8")
    assert "add server codex_lb_slots/surge 172.31.245.13:2455" in log
    assert "enable health codex_lb_slots/surge" in log
    assert "enable server codex_lb_slots/surge" in log
    assert "add server codex_lb_slots/amber" in log
    green_ready = log.index("set server codex_lb_slots/green weight 1")
    blue_drain = log.index("set server codex_lb_slots/blue weight 0")
    assert green_ready < blue_drain

    log_path.write_text("", encoding="utf-8")
    second_deploy = _run_workflow(
        root,
        fake_docker,
        "deploy",
        legacy_haproxy=True,
        surge_ip="172.31.245.14",
    )
    assert second_deploy.returncode == 0, second_deploy.stderr
    second_log = log_path.read_text(encoding="utf-8")
    assert "set server codex_lb_slots/surge addr 172.31.245.14 port 2455" in second_log


def test_failed_legacy_runtime_registration_does_not_drain_the_active_backend(
    tmp_path: Path,
) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    state_dir = root / ".codex-lb-ha"
    (state_dir / "active-slot").write_text("blue\n", encoding="utf-8")
    fake_state = root / ".fake-docker-state"
    (fake_state / "green.weight").write_text("0\n", encoding="utf-8")
    log_path = root / "docker.log"
    log_path.write_text("", encoding="utf-8")

    deploy = _run_workflow(
        root,
        fake_docker,
        "deploy",
        fail_match="add server codex_lb_slots/surge",
        legacy_haproxy=True,
    )

    assert deploy.returncode != 0
    assert "surge backend failed readiness" in deploy.stderr
    assert (fake_state / "blue.weight").read_text(encoding="utf-8").strip() != "0"
    assert (fake_state / "green.weight").read_text(encoding="utf-8").strip() == "0"
    log = log_path.read_text(encoding="utf-8")
    assert "stop server-blue" not in log
    assert "stop server-green" not in log


@pytest.mark.parametrize(
    "failed_stage",
    [
        "up --detach --build --no-deps server-surge",
        "http://server-surge:2455/health/ready",
        "set server codex_lb_slots/surge weight 1",
        "http://127.0.0.1:2455/health/ready",
    ],
)
def test_failed_surge_stage_keeps_both_base_backends_untouched(
    tmp_path: Path,
    failed_stage: str,
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
    assert "surge backend failed readiness" in deploy.stderr
    assert (root / ".codex-lb-ha/active-slot").read_text(encoding="utf-8").strip() == ("blue,green,amber")
    log = log_path.read_text(encoding="utf-8")
    assert "stop server-blue" not in log
    assert "stop server-green" not in log


def test_interrupted_base_replacement_is_resumable(tmp_path: Path) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    failed = _run_workflow(
        root,
        fake_docker,
        "deploy",
        fail_match="up --detach --no-build --no-deps --force-recreate server-blue",
    )
    assert failed.returncode != 0
    phase_file = root / ".codex-lb-ha/draining-slot"
    assert phase_file.read_text(encoding="utf-8").strip() == "replacing:blue:green,amber"
    fake_state = root / ".fake-docker-state"
    assert (fake_state / "blue.weight").read_text(encoding="utf-8").strip() == "0"
    assert (fake_state / "green.weight").read_text(encoding="utf-8").strip() != "0"
    assert (fake_state / "surge.weight").read_text(encoding="utf-8").strip() != "0"

    resumed = _run_workflow(root, fake_docker, "deploy")

    assert resumed.returncode == 0, resumed.stderr
    assert not phase_file.exists()
    assert (root / ".codex-lb-ha/active-slot").read_text(encoding="utf-8").strip() == ("blue,green,amber")


def test_fake_docker_workflow_rolls_back_the_current_base_drain(
    tmp_path: Path,
) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    state_dir = root / ".codex-lb-ha"
    (state_dir / "draining-slot").write_text("draining:blue:green\n", encoding="utf-8")
    fake_state = root / ".fake-docker-state"
    (fake_state / "blue.weight").write_text("0\n", encoding="utf-8")
    (fake_state / "surge.weight").write_text("100\n", encoding="utf-8")
    log_path = root / "docker.log"
    log_path.write_text("", encoding="utf-8")

    rollback = _run_workflow(root, fake_docker, "rollback")

    assert rollback.returncode == 0, rollback.stderr
    assert (state_dir / "active-slot").read_text(encoding="utf-8").strip() == ("blue,green,amber")
    assert not (state_dir / "draining-slot").exists()
    log = log_path.read_text(encoding="utf-8")
    ready = log.index("set server codex_lb_slots/blue weight 1")
    surge_drain = log.index("set server codex_lb_slots/surge weight 0")
    surge_stop = log.index("stop server-surge")
    assert ready < surge_drain < surge_stop
    assert "stop server-green" not in log


def test_status_reports_all_backends_and_eligible_count(tmp_path: Path) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    status = _run_workflow(root, fake_docker, "status")

    assert status.returncode == 0, status.stderr
    assert "Serving topology: blue,green,amber" in status.stdout
    assert "blue: status=UP" in status.stdout
    assert "green: status=UP" in status.stdout
    assert "amber: status=UP" in status.stdout
    assert "surge: status=DRAIN" in status.stdout
    assert "Eligible backends: 3" in status.stdout


def test_second_deploy_is_rejected_during_a_published_drain(tmp_path: Path) -> None:
    root, fake_docker = _workflow_repo(
        tmp_path,
        "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n",
    )
    bootstrap = _run_workflow(root, fake_docker, "bootstrap")
    assert bootstrap.returncode == 0, bootstrap.stderr

    state_dir = root / ".codex-lb-ha"
    (state_dir / "draining-slot").write_text("draining:blue:green\n", encoding="utf-8")
    fake_state = root / ".fake-docker-state"
    (fake_state / "blue.weight").write_text("0\n", encoding="utf-8")
    (fake_state / "surge.weight").write_text("100\n", encoding="utf-8")
    log_path = root / "docker.log"
    log_path.write_text("", encoding="utf-8")

    deploy = _run_workflow(root, fake_docker, "deploy")

    assert deploy.returncode != 0
    assert "blue is still draining" in deploy.stderr
    log = log_path.read_text(encoding="utf-8")
    assert "runtime:set server" not in log
    assert " up " not in log
    assert " stop " not in log


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
    assert "surge" in result.stdout


@pytest.mark.parametrize("slot,remaining", [("green", "amber"), ("amber", "")])
def test_each_replacement_can_resume_without_rebuilding(tmp_path: Path, slot: str, remaining: str) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    failed = _run_workflow(root, fake, "deploy", fail_match=f"--force-recreate server-{slot}")
    assert failed.returncode != 0
    phase = root / ".codex-lb-ha/draining-slot"
    assert phase.read_text().strip() == f"replacing:{slot}:{remaining}"
    log_path = root / "docker.log"
    log_path.write_text("")
    resumed = _run_workflow(root, fake, "deploy")
    assert resumed.returncode == 0, resumed.stderr
    assert "--build" not in log_path.read_text()
    assert not phase.exists()


@pytest.mark.parametrize("stage", ["haproxy -c -f", "kill --signal USR2"])
def test_failed_proxy_reload_keeps_base_and_surge_serving(tmp_path: Path, stage: str) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    failed = _run_workflow(root, fake, "deploy", fail_match=stage)
    assert failed.returncode != 0
    assert (root / ".codex-lb-ha/draining-slot").read_text().strip() == "reloading:haproxy:"
    for slot in ("blue", "green", "amber", "surge"):
        assert int((root / f".fake-docker-state/{slot}.weight").read_text()) > 0
    resumed = _run_workflow(root, fake, "deploy")
    assert resumed.returncode == 0, resumed.stderr


def test_reload_is_after_all_replacements_and_before_surge_retirement(tmp_path: Path) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    log_path = root / "docker.log"
    log_path.write_text("")
    result = _run_workflow(root, fake, "deploy")
    assert result.returncode == 0, result.stderr
    log = log_path.read_text()
    assert log.index("--force-recreate server-amber") < log.index("kill --signal USR2")
    assert log.index("kill --signal USR2") < log.index("set server codex_lb_slots/surge weight 0")
    assert "up --detach --no-deps haproxy" not in log


def test_amber_drain_can_be_cancelled_without_reverting_other_slots(tmp_path: Path) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    (root / ".codex-lb-ha/draining-slot").write_text("draining:amber:\n")
    (root / ".fake-docker-state/amber.weight").write_text("0\n")
    (root / ".fake-docker-state/surge.weight").write_text("1\n")
    result = _run_workflow(root, fake, "rollback")
    assert result.returncode == 0, result.stderr
    assert int((root / ".fake-docker-state/amber.weight").read_text()) > 0


def test_old_proxy_workers_force_bounded_drain(tmp_path: Path) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    result = _run_workflow(root, fake, "deploy", workers="2")
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("Drain bound reached") == 4


def test_unknown_old_worker_state_never_stops_a_draining_backend(tmp_path: Path) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    log_path = root / "docker.log"
    log_path.write_text("")
    result = _run_workflow(root, fake, "deploy", workers="unreadable")
    assert result.returncode != 0
    assert "stop server-blue" not in log_path.read_text()
    assert (root / ".codex-lb-ha/draining-slot").read_text().strip() == "draining:blue:green,amber"


def test_legacy_rollback_retains_candidate_without_rebuilding_serving_surge(tmp_path: Path) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    (root / ".codex-lb-ha/active-slot").write_text("blue,green\n")
    (root / ".codex-lb-ha/draining-slot").write_text("draining:blue:green\n")
    for slot, weight in [("blue", 0), ("amber", 0), ("surge", 1)]:
        (root / f".fake-docker-state/{slot}.weight").write_text(str(weight))
    rollback = _run_workflow(root, fake, "rollback")
    assert rollback.returncode == 0, rollback.stderr
    assert (root / ".codex-lb-ha/draining-slot").read_text().strip() == "retained:surge:blue,green,amber"
    log_path = root / "docker.log"
    log_path.write_text("")
    resumed = _run_workflow(root, fake, "deploy")
    assert resumed.returncode == 0, resumed.stderr
    assert "--build" not in log_path.read_text()
    assert "--force-recreate server-surge" not in log_path.read_text()


def test_unrecorded_live_surge_is_not_recreated(tmp_path: Path) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    (root / ".fake-docker-state/surge.weight").write_text("1")
    log_path = root / "docker.log"
    log_path.write_text("")
    result = _run_workflow(root, fake, "deploy")
    assert result.returncode != 0
    assert "refusing to recreate" in result.stderr
    assert " up " not in log_path.read_text()
    assert " stop " not in log_path.read_text()


def test_resume_does_not_recreate_candidate_already_readmitted(tmp_path: Path) -> None:
    root, fake = _workflow_repo(tmp_path, "CODEX_LB_DATABASE_URL=postgresql+asyncpg://db/codex_lb\n")
    assert _run_workflow(root, fake, "bootstrap").returncode == 0
    (root / ".codex-lb-ha/draining-slot").write_text("replacing:blue:green,amber\n")
    (root / ".fake-docker-state/surge.weight").write_text("1")
    log_path = root / "docker.log"
    log_path.write_text("")
    resumed = _run_workflow(root, fake, "deploy")
    assert resumed.returncode == 0, resumed.stderr
    assert "--force-recreate server-blue" not in log_path.read_text()
    assert "--force-recreate server-green" in log_path.read_text()
