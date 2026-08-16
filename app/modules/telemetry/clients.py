from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

CLIENT_FAMILY_BY_RAW_GROUP: dict[str, str] = {
    "codex_exec": "codex-cli",
    "codex-tui": "codex-cli",
    "Codex Desktop": "codex-desktop",
    "codex_vscode": "codex-vscode",
    "AsyncOpenAI": "openai-sdk-python",
    "OpenAI": "openai-sdk-js",
    "ai": "vercel-ai-sdk",
    "ai-sdk": "vercel-ai-sdk",
    "opencode": "opencode",
    "Mozilla": "browser",
    "curl": "script",
    "undici": "script",
    "node": "script",
    "Python-urllib": "script",
    "python-requests": "script",
    "aiohttp": "script",
}

CANONICAL_CLIENT_FAMILIES = frozenset({*CLIENT_FAMILY_BY_RAW_GROUP.values(), "other"})


@dataclass(frozen=True, slots=True)
class ClientCount:
    raw_group: str | None
    requests: int


def client_family(raw_group: str | None) -> str:
    return CLIENT_FAMILY_BY_RAW_GROUP.get(raw_group or "", "other")


def client_shares(rows: Iterable[ClientCount]) -> tuple[dict[str, float], float]:
    counts: defaultdict[str, int] = defaultdict(int)
    total = 0
    for row in rows:
        requests = max(0, row.requests)
        family = client_family(row.raw_group)
        if family not in CANONICAL_CLIENT_FAMILIES:
            raise ValueError(f"non-canonical telemetry client family: {family}")
        counts[family] += requests
        total += requests
    if total == 0:
        return {}, 0.0
    shares = {family: _ratio(count, total) for family, count in sorted(counts.items()) if count > 0}
    return shares, shares.get("other", 0.0)


def catalog_model_name(model: str | None, catalog: frozenset[str]) -> str:
    normalized = (model or "").strip()
    return normalized if normalized in catalog else "other"


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 6) if denominator else 0.0
