from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _compose() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((repo_root / "docker-compose.yml").read_text(encoding="utf-8"))


def test_postgres18_compose_upgrade_helper_is_digest_pinned() -> None:
    services = _compose()["services"]
    postgres = services["postgres"]
    upgrade = services["postgres-upgrade"]

    assert postgres["image"] == "postgres:18-alpine"
    assert postgres["volumes"] == ["codex-lb-postgres-data:/var/lib/postgresql"]

    entrypoint = postgres["entrypoint"]
    assert entrypoint[:2] == ["sh", "-ceu"]
    guard = entrypoint[2]
    assert "/var/lib/postgresql/PG_VERSION" in guard
    assert "/var/lib/postgresql/data/PG_VERSION" in guard
    assert "docker-entrypoint.sh" in guard

    image = upgrade["image"]
    assert image.startswith("pgautoupgrade/pgautoupgrade:18-alpine@sha256:")
    assert len(image.rsplit("@sha256:", 1)[1]) == 64
    assert upgrade["profiles"] == ["postgres-upgrade"]
    assert upgrade["environment"]["PGAUTO_ONESHOT"] == "yes"
    assert upgrade["volumes"] == ["codex-lb-postgres-data:/var/lib/postgresql"]
    assert upgrade["restart"] == "no"
