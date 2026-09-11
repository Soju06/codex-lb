"""Explicit server-local LLMBox login binding; never performs interactive login."""

from __future__ import annotations

from pathlib import Path

LLMBOX_KIND = "llmbox"
LLMBOX_BASE_URL = "https://llmbox.bytedance.net/v1"


class LLMBoxCredentialsError(ValueError):
    pass


def read_access_token() -> str:
    try:
        lines = (Path.home() / ".llmbox/cache/accesstoken").read_text().splitlines()
        token = lines[1].strip()
        if not token or any(char.isspace() for char in token):
            raise ValueError("Malformed cache")
        return "at-" + token
    except (OSError, UnicodeError, IndexError, ValueError):
        raise LLMBoxCredentialsError("LLMBox login cache unavailable; use the normal LLMBox login flow") from None


def cache_state() -> str:
    try:
        read_access_token()
        return "present"
    except LLMBoxCredentialsError:
        return "unavailable"


def validate_binding(base_url: str, api_key: str | bytes | None, audio: bool, embeddings: bool) -> None:
    if base_url != LLMBOX_BASE_URL:
        raise ValueError("LLMBox requires its fixed HTTPS base URL")
    if api_key is not None:
        raise ValueError("LLMBox uses the server-local login cache, not a supplied API key")
    if audio or embeddings:
        raise ValueError("LLMBox audio and embeddings have not been verified")
