"""Local TRAE login binding and explicit catalog metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ValidationError

TRAE_KIND = "trae"
TRAE_BASE_URL = "https://copilot-cn.bytedance.net/api/ide/v2"


class TraeCredentialsError(ValueError):
    pass


class _Credential(BaseModel):
    access_token: str
    credential_kind: str
    region: str


class _Login(BaseModel):
    trae: _Credential


def auth_headers() -> dict[str, str]:
    try:
        login = _Login.model_validate_json((Path.home() / ".trae/cli/auth.json").read_text())
        auth = login.trae
        schemes = {
            "cloud_cli_jwt": "Cloud-CLI-JWT",
            "cloud_ide_jwt": "Cloud-IDE-JWT",
            "byte_cloud_jwt": "Byte-Cloud-JWT",
            "codebase_user_jwt": "Codebase-User-JWT",
            "trae_oauth": "Bearer",
        }
        if auth.region != "CN" or auth.credential_kind not in schemes:
            raise ValueError("Unsupported login")
        if not auth.access_token or any(c.isspace() for c in auth.access_token):
            raise ValueError("Malformed token")
        return {"Authorization": f"{schemes[auth.credential_kind]} {auth.access_token}"}
    except (OSError, UnicodeError, ValueError, ValidationError):
        raise TraeCredentialsError("TRAE CN login unavailable; complete the normal TRAE login flow") from None


def cache_state() -> str:
    try:
        auth_headers()
        return "present"
    except TraeCredentialsError:
        return "unavailable"


def validate_binding(base_url: str, api_key: str | bytes | None, audio: bool, embeddings: bool) -> None:
    if base_url != TRAE_BASE_URL:
        raise ValueError("TRAE requires its fixed CN HTTPS gateway")
    if api_key is not None:
        raise ValueError("TRAE uses the server-local login, not a supplied API key")
    if audio or embeddings:
        raise ValueError("TRAE audio and embeddings are not supported")


def request_headers(config_name: str | None = None) -> dict[str, str]:
    headers = {
        **auth_headers(),
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": "TraeClient/TTNet",
        "x-app-id": "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8",
        "x-app-version": "default",
        "x-ide-version": "3.3.72",
        "x-ide-version-code": "20260630",
        "x-app-version-code": "20260630",
        "x-ide-function": "solo_agent",
        "request-traffic-type": "prod",
    }

    if config_name is not None and config_name.startswith("gemini-"):
        headers["x-ide-function"] = "traecli_next"
        headers["x-ide-version-code"] = datetime.now(timezone.utc).strftime("%Y%m%d")
    return headers
