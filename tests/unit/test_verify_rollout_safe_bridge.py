from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import verify_rollout_safe_bridge as verify_script

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_trigger_restart_uses_configured_kubectl_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _fake_run(cmd: list[str], *, check: bool, capture_output: bool, text: bool):
        seen["cmd"] = cmd
        assert check is False
        assert capture_output is True
        assert text is True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    sleep = AsyncMock()
    monkeypatch.setattr(verify_script.asyncio, "sleep", sleep)
    monkeypatch.setattr(verify_script.subprocess, "run", _fake_run)

    config = verify_script.VerifyConfig(
        base_url="http://127.0.0.1:18081/v1/responses",
        host="codex-lb.localtest.me",
        api_key="sk-test",
        model="gpt-5.1",
        sessions=1,
        turns=1,
        restart=True,
        restart_delay_seconds=2.0,
        rollout_namespace="codex-lb-e2e",
        rollout_context="kind-codex-lb-local",
        rollout_target="statefulset/codex-lb-e2e-workload",
        delete_owner_after_first_turn=False,
        postgresql_pod="postgresql-0",
        postgresql_user="codexlb",
        postgresql_database="codexlb",
        postgresql_secret="postgresql",
        kubectl_binary="/tmp/custom-kubectl",
        session_prefix="verify-test",
    )

    await verify_script._trigger_restart(config)

    sleep.assert_called_once_with(2.0)
    assert seen["cmd"] == [
        "/tmp/custom-kubectl",
        "--context",
        "kind-codex-lb-local",
        "-n",
        "codex-lb-e2e",
        "rollout",
        "restart",
        "statefulset/codex-lb-e2e-workload",
    ]
