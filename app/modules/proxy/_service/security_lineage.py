from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any

from app.modules.proxy._service.support import _security_lineage_ids
from app.modules.proxy.durable_bridge_repository import durable_bridge_api_key_scope

logger = logging.getLogger(__name__)


class _SecurityLineageMixin:
    _repo_factory: Any

    async def _security_lineage_requires_security_work_authorized(
        self,
        lineage_ids: Collection[str],
        *,
        api_key_id: str | None,
    ) -> bool:
        normalized_lineage_ids = _security_lineage_ids(*lineage_ids)
        if not normalized_lineage_ids:
            return False
        try:
            async with self._repo_factory() as repos:
                return (
                    await repos.sticky_sessions.security_work_required(
                        normalized_lineage_ids,
                        api_key_scope=durable_bridge_api_key_scope(api_key_id),
                    )
                    is True
                )
        except Exception:
            # Unknown lineage metadata must not downgrade a previously
            # classified security request into ordinary account selection.
            logger.warning("Security-work lineage lookup failed; requiring an authorized account", exc_info=True)
            return True
