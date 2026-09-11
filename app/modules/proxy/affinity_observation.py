from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.modules.proxy.affinity import _AffinityPolicy


@dataclass(frozen=True, slots=True)
class AffinityObservation:
    """Content-free snapshot carried by one request or logged attempt."""

    source: str
    kind: str | None
    key_hash: str | None

    @classmethod
    def from_policy(cls, source: str, policy: _AffinityPolicy) -> AffinityObservation:
        key = policy.selection_key
        return cls(
            source=source,
            kind=policy.kind.value if policy.kind is not None else None,
            key_hash=hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key is not None else None,
        )
