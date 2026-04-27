from __future__ import annotations

import pytest

from app.core.runtime.bridge_worker_pool import build_worker_configs, build_worker_env

pytestmark = pytest.mark.unit


def test_build_worker_configs_makes_unique_loopback_endpoints(monkeypatch):
    monkeypatch.delenv("CODEX_LB_BRIDGE_WORKER_BASE_PORT", raising=False)

    workers = build_worker_configs(worker_count=3, public_port=2455, instance_id_base="codex-lb")

    assert [worker.instance_id for worker in workers] == [
        "codex-lb-worker-1",
        "codex-lb-worker-2",
        "codex-lb-worker-3",
    ]
    assert [worker.port for worker in workers] == [3455, 3456, 3457]
    assert [worker.base_url for worker in workers] == [
        "http://127.0.0.1:3455",
        "http://127.0.0.1:3456",
        "http://127.0.0.1:3457",
    ]


def test_build_worker_configs_uses_explicit_base_port(monkeypatch):
    monkeypatch.setenv("CODEX_LB_BRIDGE_WORKER_BASE_PORT", "4500")

    workers = build_worker_configs(worker_count=2, public_port=2455, instance_id_base="bridge")

    assert [worker.port for worker in workers] == [4500, 4501]


def test_build_worker_configs_rejects_single_worker():
    with pytest.raises(ValueError, match="greater than one"):
        build_worker_configs(worker_count=1, public_port=2455, instance_id_base="bridge")


def test_build_worker_env_sets_addressable_bridge_owner():
    worker = build_worker_configs(worker_count=2, public_port=2455, instance_id_base="bridge")[0]

    env = build_worker_env(worker, base_env={"CODEX_LB_UVICORN_WORKERS": "8"})

    assert env["PORT"] == "3455"
    assert env["CODEX_LB_UVICORN_WORKERS"] == "1"
    assert env["UVICORN_WORKERS"] == "1"
    assert env["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID"] == "bridge-worker-1"
    assert env["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL"] == "http://127.0.0.1:3455"


def test_build_worker_env_disables_shared_background_loops_for_followers():
    follower = build_worker_configs(worker_count=2, public_port=2455, instance_id_base="bridge")[1]

    env = build_worker_env(
        follower,
        base_env={
            "CODEX_LB_METRICS_ENABLED": "true",
            "CODEX_LB_MODEL_REGISTRY_ENABLED": "true",
            "CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED": "true",
            "CODEX_LB_USAGE_REFRESH_ENABLED": "true",
        },
    )

    assert env["CODEX_LB_METRICS_ENABLED"] == "false"
    assert env["CODEX_LB_MODEL_REGISTRY_ENABLED"] == "true"
    assert env["CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED"] == "false"
    assert env["CODEX_LB_USAGE_REFRESH_ENABLED"] == "false"
