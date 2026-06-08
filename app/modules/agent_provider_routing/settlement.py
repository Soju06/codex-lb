from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentProviderUsageSettlementData:
    requests: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
