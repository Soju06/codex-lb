from __future__ import annotations

from typing import TypedDict


class UpstreamError(TypedDict, total=False):
    message: str
    resets_at: int | float
    resets_in_seconds: int | float
