from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "deploy" / "onda" / "codex-lb.env.example"
COMPOSE_PATH = ROOT / "deploy" / "onda" / "docker-compose.yml"


def _env() -> dict[str, str]:
    return {
        key: value
        for line in ENV_PATH.read_text().splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", 1)]
    }


def test_onda_deployment_forces_auth_retention_and_payload_controls() -> None:
    env = _env()
    assert env["CODEX_LB_DASHBOARD_ACCESS_JWT_REQUIRED"] == "true"
    assert env["CODEX_LB_DASHBOARD_ACCESS_JWT_ISSUER"] == "https://onda-hq.cloudflareaccess.com"
    assert env["CODEX_LB_DASHBOARD_ACCESS_ALLOWED_EMAIL_DOMAINS"] == "onda.lol"
    assert env["CODEX_LB_REQUEST_LOG_RETENTION_DAYS"] == "30"
    assert env["CODEX_LB_REQUEST_LOG_STORE_ERROR_DETAILS"] == "false"
    assert env["CODEX_LB_DATABASE_SQLITE_PRE_MIGRATE_BACKUP_MAX_AGE_DAYS"] == "30"
    assert env["CODEX_LB_CONVERSATION_ARCHIVE_ENABLED"] == "false"
    assert env["CODEX_LB_LOG_PROXY_REQUEST_PAYLOAD"] == "false"
    assert env["CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD"] == "false"
    assert env["CODEX_LB_LOG_PROXY_REQUEST_SHAPE_RAW_CACHE_KEY"] == "false"


def test_onda_deployment_has_no_published_ports_and_binds_loopback() -> None:
    compose = COMPOSE_PATH.read_text()
    assert "network_mode: host" in compose
    assert "--host\n      - 127.0.0.1" in compose
    assert "ports:" not in compose
    assert "0.0.0.0" not in compose
