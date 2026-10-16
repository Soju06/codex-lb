"""Private local credential files read by the desktop bridge.

Both files are re-read on every request so Codex token refreshes and LB key
rotations apply without a restart. Nothing here writes either file, and error
messages never contain a path or a token.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

AUTH_FILE_NAME: Final[str] = "auth.json"
MAX_AUTH_FILE_BYTES: Final[int] = 256 * 1024
MAX_TOKEN_FILE_BYTES: Final[int] = 4 * 1024

_OPEN_FLAGS: Final[int] = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)
# POSIX only; platforms without uids (Windows) skip the owner and mode check.
_CURRENT_UID: Final[Callable[[], int] | None] = getattr(os, "getuid", None)
_AUTH_LABEL: Final[str] = "Codex auth.json"
_TOKEN_LABEL: Final[str] = "The LB token file"
_SIGN_IN_AGAIN: Final[str] = "sign in to Codex with ChatGPT again"


class CredentialError(ValueError):
    """A local credential file is missing, not private, or malformed."""


@dataclass(frozen=True, slots=True)
class LocalCredentials:
    """Hold the current Desktop and LB tokens without exposing them in repr output."""

    access_token: str = field(repr=False)
    lb_token: str = field(repr=False)


def load_credentials(codex_home: Path, lb_token_file: Path) -> LocalCredentials:
    """Re-read both private files and return validated tokens, or raise CredentialError."""
    return LocalCredentials(access_token=read_access_token(codex_home), lb_token=read_lb_token(lb_token_file))


def is_visible_ascii(value: str) -> bool:
    """True for a non-empty string of printable ASCII without spaces (0x21-0x7E)."""
    return bool(value) and value.isascii() and value.isprintable() and " " not in value


def read_access_token(codex_home: Path) -> str:
    """Read a file-backed ChatGPT token; reject API-key logins and malformed credentials."""
    raw = _read_private_file(codex_home / AUTH_FILE_NAME, MAX_AUTH_FILE_BYTES, _AUTH_LABEL)
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError):
        raise CredentialError(f"{_AUTH_LABEL} is not valid JSON; {_SIGN_IN_AGAIN}.") from None
    if not isinstance(document, dict):
        raise CredentialError(f"{_AUTH_LABEL} is not a Codex login file; {_SIGN_IN_AGAIN}.")
    # Older snapshots omit auth_mode; an explicit non-ChatGPT mode is never bridged.
    if "auth_mode" in document and document["auth_mode"] != "chatgpt":
        raise CredentialError(f"{_AUTH_LABEL} is not a ChatGPT login; {_SIGN_IN_AGAIN}.")
    if document.get("OPENAI_API_KEY") not in (None, ""):
        raise CredentialError(f"{_AUTH_LABEL} contains an API key; {_SIGN_IN_AGAIN}.")
    tokens = document.get("tokens")
    access_token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(access_token, str) or not is_visible_ascii(access_token):
        raise CredentialError(f"{_AUTH_LABEL} has no usable ChatGPT access token; {_SIGN_IN_AGAIN}.")
    return access_token


def read_lb_token(token_file: Path) -> str:
    """Read one visible-ASCII LB key, allowing a single optional LF or CRLF terminator."""
    raw = _read_private_file(token_file, MAX_TOKEN_FILE_BYTES, _TOKEN_LABEL)
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith(b"\n"):
        raw = raw[:-1]
    # Non-ASCII bytes decode to U+FFFD and then fail the visible-ASCII check.
    token = raw.decode("ascii", errors="replace")
    if not is_visible_ascii(token):
        raise CredentialError(f"{_TOKEN_LABEL} must contain exactly one line holding the codex-lb API key.")
    return token


def _read_private_file(path: Path, limit: int, label: str) -> bytes:
    """Read a bounded regular file, enforcing no-follow and owner-only access where supported."""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError:
        raise CredentialError(f"{label} is missing, unreadable, or a symlink.") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CredentialError(f"{label} must be a regular file.")
        if _CURRENT_UID is not None and (info.st_uid != _CURRENT_UID() or info.st_mode & 0o077):
            raise CredentialError(f"{label} must be owned by you with no group or other access (chmod 600).")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        raise CredentialError(f"{label} is unreadable.") from None
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > limit:
        raise CredentialError(f"{label} is too large.")
    return data
