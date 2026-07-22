from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_instance_mismatch_total,
)
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_durable_lease_ttl_seconds,
    _is_missing_durable_bridge_table_error,
    _log_http_bridge_event,
    _reconcile_durable_http_bridge_ownership,
    _record_bridge_reattach,
    _renew_durable_http_bridge_lease,
)
from app.modules.proxy._service.http_bridge.service_stubs import (
    _headers_with_turn_state,
    _service_get_settings,
)
from app.modules.proxy._service.support import _HTTPBridgeSession
from app.modules.proxy.affinity import _extract_model_class
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup

logger = logging.getLogger(__name__)


class _HTTPBridgeDurableSessionsMixin:
    async def _claim_durable_http_bridge_session(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        allow_takeover: bool,
        force_owner_epoch_advance: bool = False,
        claim_account_id: str | None = None,
        clear_latest_turn_state: bool = False,
    ) -> None:
        current_instance = _service_get_settings().http_responses_session_bridge_instance_id
        try:
            lookup: DurableBridgeLookup | None = None
            for claim_attempt in range(2):
                lookup = await self._durable_bridge.claim_live_session(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    instance_id=current_instance,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                    account_id=claim_account_id or session.account.id,
                    model=session.request_model,
                    service_tier=getattr(session, "request_service_tier", None),
                    latest_turn_state=None if clear_latest_turn_state else session.downstream_turn_state,
                    latest_response_id=None,
                    allow_takeover=allow_takeover,
                    force_owner_epoch_advance=force_owner_epoch_advance or claim_attempt > 0,
                )
                if lookup.owner_instance_id == current_instance:
                    break
                if not allow_takeover or claim_attempt > 0:
                    break
                await asyncio.sleep(0)
            assert lookup is not None
            if lookup.owner_instance_id != current_instance:
                _log_http_bridge_event(
                    "owner_mismatch_retry",
                    session.key,
                    account_id=None,
                    model=session.request_model,
                    detail=(
                        "expected_instance="
                        f"{lookup.owner_instance_id}, current_instance={current_instance}, outcome=claim_rejected"
                    ),
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    owner_check_applied=True,
                )
                if PROMETHEUS_AVAILABLE and bridge_instance_mismatch_total is not None:
                    bridge_instance_mismatch_total.labels(outcome="retry").inc()
                raise ProxyResponseError(
                    409,
                    openai_error(
                        "bridge_instance_mismatch",
                        "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
                        error_type="server_error",
                    ),
                )
            session.durable_session_id = lookup.session_id
            session.durable_owner_epoch = lookup.owner_epoch
            session.headers = _headers_with_turn_state(session.headers, session.downstream_turn_state)
            if (
                PROMETHEUS_AVAILABLE
                and bridge_durable_recover_total is not None
                and allow_takeover
                and lookup.owner_epoch > 1
            ):
                bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _record_bridge_reattach(path="restart_takeover", outcome="success")
            if session.key.affinity_kind == "session_header":
                await self._durable_bridge.register_session_header(
                    session_id=lookup.session_id,
                    api_key_id=session.key.api_key_id,
                    session_header=session.key.affinity_key,
                )
        except Exception as exc:
            if _is_missing_durable_bridge_table_error(exc):
                logger.warning("Durable bridge tables missing; using in-memory bridge session fallback", exc_info=True)
                return
            raise

    async def _refresh_durable_http_bridge_session(self: Any, session: _HTTPBridgeSession) -> None:
        """Renew the durable lease; callers must hold ``self._http_bridge_lock``."""

        await _renew_durable_http_bridge_lease(self, session)

    async def reconcile_durable_http_bridge_ownership(self: Any) -> int:
        """Close local sessions whose durable row is owned by another instance/epoch."""

        return await _reconcile_durable_http_bridge_ownership(self)
