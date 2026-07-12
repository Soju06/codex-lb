from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("compose_name", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_stock_compose_uses_user_defined_default_bridge(compose_name: str) -> None:
    compose: dict[str, Any] = yaml.safe_load((_REPO_ROOT / compose_name).read_text(encoding="utf-8"))

    assert compose["networks"]["default"] == {"driver": "bridge"}
    for service in compose["services"].values():
        assert service.get("network_mode") != "bridge"
        assert "dns" not in service


def test_standalone_docker_examples_use_named_bridge() -> None:
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    standalone_launches = readme.count("docker run -d --name codex-lb")
    portable_launches = readme.count("--network codex-lb-net")
    host_network_launches = readme.count("--network host")

    assert standalone_launches > 0
    assert portable_launches + host_network_launches == standalone_launches
    assert (
        readme.count("docker network inspect codex-lb-net >/dev/null 2>&1 || docker network create codex-lb-net")
        == portable_launches
    )
    assert "--dns " not in readme


def test_linux_roaming_example_uses_host_resolver_path() -> None:
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    roaming_section = readme.split("### Linux Wi-Fi roaming", 1)[1].split("## Remote Setup", 1)[0]

    assert "--network host" in roaming_section
    assert "127.0.0.53" not in roaming_section
    assert " -p " not in roaming_section
    assert "embedded DNS" in roaming_section
    assert "retain external forwarding servers" in roaming_section


def test_running_container_resolver_runbook_uses_bridge_scoped_systemd_listener() -> None:
    context = (_REPO_ROOT / "openspec/specs/deployment-networking/context.md").read_text(encoding="utf-8")

    assert "DNSStubListenerExtra=%s" in context
    assert "docker exec --user 0 codex-lb" in context
    assert "without restarting codex-lb" in context
    assert "rather than `0.0.0.0`" in context
