from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_repo_file(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _read_compose(relative_path: str) -> dict[str, Any]:
    return yaml.safe_load(_read_repo_file(relative_path))


def test_development_compose_disables_uvicorn_proxy_header_processing():
    compose = _read_compose("docker-compose.yml")
    command = compose["services"]["server"]["command"]

    assert command[:2] == ["uvicorn", "app.main:app"]
    assert command.count("--no-proxy-headers") == 1


@pytest.mark.parametrize(
    ("relative_path", "command"),
    [
        ("README.md", "uv run fastapi run app/main.py --reload --no-proxy-headers"),
        ("README.zh-CN.md", "uv run fastapi run app/main.py --reload --no-proxy-headers"),
        (
            "openspec/specs/responses-api-compat/ops.md",
            ".venv/bin/fastapi run app/main.py --host 127.0.0.1 --port 2460 --no-proxy-headers",
        ),
    ],
)
def test_documented_direct_starters_disable_uvicorn_proxy_header_processing(relative_path: str, command: str):
    assert command in _read_repo_file(relative_path)


@pytest.mark.parametrize(
    ("relative_path", "contract"),
    [
        ("Dockerfile", 'CMD ["/app/scripts/docker-entrypoint.sh"]'),
        ("scripts/docker-entrypoint.sh", "exec python -m app.cli"),
        ("Dockerfile.distroless", 'CMD ["python", "/app/scripts/distroless-entrypoint.py"]'),
        ("scripts/distroless-entrypoint.py", '[sys.executable, "-m", "app.cli"'),
        ("deploy/helm/codex-lb/templates/deployment.yaml", "            - app.cli\n"),
    ],
)
def test_owned_production_launchers_delegate_to_app_cli(relative_path: str, contract: str):
    assert contract in _read_repo_file(relative_path)


def test_production_compose_inherits_the_dockerfile_launcher():
    server = _read_compose("docker-compose.prod.yml")["services"]["server"]

    assert server["build"]["dockerfile"] == "Dockerfile"
    assert "command" not in server
