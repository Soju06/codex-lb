from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _compose() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))


def test_mysql_compose_service_matches_make_test_mysql_defaults() -> None:
    mysql = _compose()["services"]["mysql"]

    # ``make test-mysql`` defaults to mysql+asyncmy://codex_lb:codex_lb@127.0.0.1:3306/codex_lb,
    # so the dev profile service must expose exactly that database and credentials.
    assert mysql["image"] == "mysql:8.4"
    assert mysql["profiles"] == ["mysql"]
    assert mysql["environment"] == {
        "MYSQL_ROOT_PASSWORD": "codex_lb_root",
        "MYSQL_USER": "codex_lb",
        "MYSQL_PASSWORD": "codex_lb",
        "MYSQL_DATABASE": "codex_lb",
    }
    assert mysql["ports"] == ["3306:3306"]
    assert mysql["volumes"] == ["codex-lb-mysql-data:/var/lib/mysql"]
    assert mysql["restart"] == "unless-stopped"


def test_mysql_compose_healthcheck_uses_the_codex_lb_account() -> None:
    mysql = _compose()["services"]["mysql"]

    # ``mysqladmin ping`` exits non-zero while the server is still initialising;
    # the healthcheck must gate dependents on a direct TCP ping with the app account.
    assert mysql["healthcheck"]["test"] == [
        "CMD-SHELL",
        "mysqladmin ping -h 127.0.0.1 -ucodex_lb -pcodex_lb --silent",
    ]
