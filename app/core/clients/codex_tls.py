from __future__ import annotations

from typing import Any, Final

CODEX_TLS_IMPERSONATE: Final[str] = "chrome"


def codex_tls_kwargs() -> dict[str, Any]:
    return {"impersonate": CODEX_TLS_IMPERSONATE}
