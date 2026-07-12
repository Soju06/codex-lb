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

    assert standalone_launches > 0
    assert readme.count("--network codex-lb-net") == standalone_launches
    assert (
        readme.count("docker network inspect codex-lb-net >/dev/null 2>&1 || docker network create codex-lb-net")
        == standalone_launches
    )
    assert "--dns " not in readme
