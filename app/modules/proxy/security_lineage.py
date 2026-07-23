from __future__ import annotations

import hashlib
from collections.abc import Iterable

SECURITY_WORK_MARKER_PREFIX = "@security-work/v2/"
ANONYMOUS_API_KEY_SCOPE = "__anonymous__"


def security_work_marker_key(lineage_id: str, api_key_scope: str | None) -> str:
    scope = (api_key_scope or "").strip() or ANONYMOUS_API_KEY_SCOPE
    digest = hashlib.sha256(f"{scope}\0{lineage_id}".encode()).hexdigest()
    return f"{SECURITY_WORK_MARKER_PREFIX}{digest}"


def legacy_security_work_marker_key(lineage_id: str) -> str:
    return f"{SECURITY_WORK_MARKER_PREFIX}{hashlib.sha256(lineage_id.encode()).hexdigest()}"


def security_work_marker_keys(
    lineage_ids: Iterable[str],
    *,
    api_key_scope: str | None,
    include_legacy: bool = True,
) -> tuple[str, ...]:
    keys: dict[str, None] = {}
    for lineage_id in lineage_ids:
        stripped = lineage_id.strip()
        if not stripped:
            continue
        if stripped.startswith(SECURITY_WORK_MARKER_PREFIX):
            keys[stripped] = None
            continue
        keys[security_work_marker_key(stripped, api_key_scope)] = None
        if include_legacy:
            keys[legacy_security_work_marker_key(stripped)] = None
    return tuple(keys)
