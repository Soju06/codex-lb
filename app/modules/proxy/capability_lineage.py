from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from hashlib import sha256

_MARKER_DOMAIN = "capability-lineage/v1"


@dataclass(frozen=True, slots=True)
class CapabilityLineageAlias:
    kind: str
    value: str


def normalize_capability_lineage_aliases(
    aliases: Collection[CapabilityLineageAlias],
) -> tuple[CapabilityLineageAlias, ...]:
    normalized: dict[tuple[str, str], CapabilityLineageAlias] = {}
    for alias in aliases:
        kind = alias.kind.strip()
        value = alias.value.strip()
        if kind and value:
            normalized[(kind, value)] = CapabilityLineageAlias(kind=kind, value=value)
    return tuple(normalized.values())


def capability_lineage_marker_hash(
    *,
    capability: str,
    api_key_scope: str,
    alias: CapabilityLineageAlias,
) -> str:
    normalized_capability = capability.strip()
    normalized_scope = api_key_scope.strip()
    normalized_aliases = normalize_capability_lineage_aliases((alias,))
    if not normalized_capability or not normalized_scope or not normalized_aliases:
        raise ValueError("capability, API-key scope, and lineage alias must be non-empty")
    normalized_alias = normalized_aliases[0]
    payload = "\0".join(
        (
            _MARKER_DOMAIN,
            normalized_capability,
            normalized_scope,
            normalized_alias.kind,
            normalized_alias.value,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def capability_lineage_marker_hashes(
    *,
    capability: str,
    api_key_scope: str,
    aliases: Collection[CapabilityLineageAlias],
) -> tuple[str, ...]:
    return tuple(
        capability_lineage_marker_hash(
            capability=capability,
            api_key_scope=api_key_scope,
            alias=alias,
        )
        for alias in normalize_capability_lineage_aliases(aliases)
    )
