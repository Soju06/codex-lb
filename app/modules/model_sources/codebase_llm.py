"""Codebase LLMProxy's native OpenAI-compatible model surface."""

from __future__ import annotations

from app.core.types import JsonValue
from app.modules.model_sources import trae

CODEBASE_LLM_KIND = "codebase_llm"
CODEBASE_LLM_BASE_URL = "https://codebase-api.byted.org/v2/api/2022-06-01/LLMProxy/Model"


def request_headers() -> dict[str, str]:
    scheme, _, token = trae.auth_headers()["Authorization"].partition(" ")
    if scheme not in ("Cloud-CLI-JWT", "Codebase-User-JWT"):
        raise ValueError("Codebase LLMProxy requires a Codebase-backed local TRAE login")
    return {
        "Authorization": "Bearer " + token,
        "User-Agent": "codex_cli_rs/0.77.0",
        "x-instructions-enabled": "true",
        "x-bf-agent-id": "bytesec-cli",
        "x-bf-tenant-id": "myuser-cli",
        "x-bf-project-id": "default",
    }


def cache_state() -> str:
    try:
        request_headers()
        return "present"
    except ValueError:
        return "unavailable"


def validate_binding(base_url: str, api_key: str | bytes | None, audio: bool, embeddings: bool) -> None:
    if base_url != CODEBASE_LLM_BASE_URL:
        raise ValueError("Codebase LLMProxy requires its fixed HTTPS gateway")
    if api_key is not None:
        raise ValueError("Codebase LLMProxy uses the local TRAE Codebase login")
    if audio or embeddings:
        raise ValueError("Codebase LLMProxy audio and embeddings are not supported")


def upstream_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Remove only our documented local routing namespace; preserve the model ID."""
    model = payload.get("model")
    if isinstance(model, str) and model.startswith("codebase/"):
        return {**payload, "model": model.removeprefix("codebase/")}
    return payload
