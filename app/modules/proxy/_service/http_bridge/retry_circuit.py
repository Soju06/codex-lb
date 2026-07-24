from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import anyio

from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE, http_bridge_retry_circuit_total
from app.modules.proxy._service.observability import _hash_identifier
from app.modules.proxy._service.support import _HTTPBridgeSession
from app.modules.proxy.durable_bridge_repository import DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS

logger = logging.getLogger(__name__)

_HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD = 2
_HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS = 60.0
_HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS = 600.0
_HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS = 30.0


@dataclass(slots=True)
class _HTTPBridgeRetryCircuitState:
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_detail: str | None = None


def _initialize_http_bridge_retry_circuit(service: Any) -> None:
    service._http_bridge_retry_circuits = {}
    service._http_bridge_retry_circuit_loaded_keys = set()
    service._http_bridge_retry_circuit_persisted_keys = set()
    service._http_bridge_retry_circuit_lock = anyio.Lock()


class _HTTPBridgeRetryCircuitMixin:
    async def _load_http_bridge_retry_circuit(self: Any, session: _HTTPBridgeSession) -> None:
        if session.key.strength != "hard":
            return

        async with self._http_bridge_retry_circuit_lock:
            if session.key in self._http_bridge_retry_circuit_loaded_keys:
                return
            try:
                persisted = await self._durable_bridge.lookup_retry_circuit(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                )
            except Exception:
                logger.warning(
                    "Failed to load persisted HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    exc_info=True,
                )
                return
            if persisted is None:
                return

            now_epoch = time.time()
            if now_epoch - persisted.updated_at_epoch > DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS:
                try:
                    await self._durable_bridge.clear_retry_circuit(
                        session_key_kind=session.key.affinity_kind,
                        session_key_value=session.key.affinity_key,
                        api_key_id=session.key.api_key_id,
                    )
                except Exception:
                    logger.warning(
                        "Failed to remove stale HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        exc_info=True,
                    )
                return

            self._http_bridge_retry_circuit_loaded_keys.add(session.key)
            self._http_bridge_retry_circuit_persisted_keys.add(session.key)
            cooldown_remaining = max(0.0, persisted.cooldown_until_epoch - now_epoch)
            self._http_bridge_retry_circuits[session.key] = _HTTPBridgeRetryCircuitState(
                consecutive_failures=max(0, persisted.consecutive_failures),
                cooldown_until=time.monotonic() + cooldown_remaining,
                last_detail=persisted.last_detail,
            )

    async def _persist_http_bridge_retry_circuit(
        self: Any,
        session: _HTTPBridgeSession,
        state: _HTTPBridgeRetryCircuitState,
    ) -> None:
        now_monotonic = time.monotonic()
        now_wall = time.time()
        threshold = max(1, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD)
        base_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS)
        if state.last_detail == "clean_close":
            base_backoff = min(
                base_backoff,
                max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS),
            )
        try:
            persisted = await self._durable_bridge.persist_retry_circuit(
                session_key_kind=session.key.affinity_kind,
                session_key_value=session.key.affinity_key,
                api_key_id=session.key.api_key_id,
                consecutive_failures=state.consecutive_failures,
                cooldown_until_epoch=now_wall + max(0.0, state.cooldown_until - now_monotonic),
                last_detail=state.last_detail,
                updated_at_epoch=now_wall,
                failure_threshold=threshold,
                conflict_cooldown_until_epoch=now_wall + base_backoff,
            )
            if persisted is not None:
                state.consecutive_failures = max(state.consecutive_failures, persisted.consecutive_failures)
                state.cooldown_until = max(
                    state.cooldown_until,
                    now_monotonic + max(0.0, persisted.cooldown_until_epoch - now_wall),
                )
                state.last_detail = persisted.last_detail or state.last_detail
            self._http_bridge_retry_circuit_persisted_keys.add(session.key)
        except Exception:
            logger.warning(
                "Failed to persist HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                exc_info=True,
            )

    async def _http_bridge_precreated_retry_allowed(self: Any, session: _HTTPBridgeSession) -> bool:
        """Avoid replaying a repeatedly failing hard-affinity request in a tight loop."""
        if session.key.strength != "hard":
            return True

        await self._load_http_bridge_retry_circuit(session)
        now = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            if state is None or state.cooldown_until <= now:
                if state is not None and state.cooldown_until > 0:
                    state.cooldown_until = 0.0
                    logger.info(
                        "http_bridge_retry_circuit event=half_open bridge_kind=%s bridge_key=%s failures=%s",
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        state.consecutive_failures,
                    )
                return True

            retry_after = max(0.0, state.cooldown_until - now)
            if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                http_bridge_retry_circuit_total.labels(outcome="suppressed").inc()
            logger.info(
                "http_bridge_retry_circuit event=suppressed bridge_kind=%s bridge_key=%s "
                "failures=%s retry_after_seconds=%.1f detail=%s",
                session.key.affinity_kind,
                _hash_identifier(session.key.affinity_key),
                state.consecutive_failures,
                retry_after,
                state.last_detail,
            )
            return False

    async def _http_bridge_precreated_retry_cooldown_seconds(self: Any, session: _HTTPBridgeSession) -> float:
        if session.key.strength != "hard":
            return 0.0

        await self._load_http_bridge_retry_circuit(session)
        now = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.get(session.key)
            if state is None:
                return 0.0
            return max(0.0, state.cooldown_until - now)

    async def _record_http_bridge_retry_circuit_failure(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        detail: str,
    ) -> None:
        if session.key.strength != "hard" or detail not in {"stream_incomplete", "clean_close", "stream_idle_timeout"}:
            return

        await self._load_http_bridge_retry_circuit(session)
        threshold = max(1, _HTTP_BRIDGE_RETRY_CIRCUIT_FAILURE_THRESHOLD)
        base_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_BASE_BACKOFF_SECONDS)
        max_backoff = max(base_backoff, _HTTP_BRIDGE_RETRY_CIRCUIT_MAX_BACKOFF_SECONDS)
        clean_close_max_backoff = max(0.001, _HTTP_BRIDGE_RETRY_CIRCUIT_CLEAN_CLOSE_MAX_BACKOFF_SECONDS)
        now = time.monotonic()
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.setdefault(session.key, _HTTPBridgeRetryCircuitState())
            state.consecutive_failures += 1
            state.last_detail = detail
            if state.consecutive_failures >= threshold:
                backoff = min(
                    max_backoff,
                    base_backoff * (2 ** (state.consecutive_failures - threshold)),
                )
                if detail == "clean_close":
                    backoff = min(backoff, clean_close_max_backoff)
                state.cooldown_until = now + backoff
                if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
                    http_bridge_retry_circuit_total.labels(outcome="opened").inc()
                logger.warning(
                    "http_bridge_retry_circuit event=opened bridge_kind=%s bridge_key=%s "
                    "failures=%s cooldown_seconds=%.1f detail=%s",
                    session.key.affinity_kind,
                    _hash_identifier(session.key.affinity_key),
                    state.consecutive_failures,
                    backoff,
                    detail,
                )
            await self._persist_http_bridge_retry_circuit(session, state)
            self._http_bridge_retry_circuit_loaded_keys.add(session.key)

    async def _clear_http_bridge_retry_circuit(self: Any, session: _HTTPBridgeSession) -> None:
        if session.key.strength != "hard":
            return

        await self._load_http_bridge_retry_circuit(session)
        async with self._http_bridge_retry_circuit_lock:
            state = self._http_bridge_retry_circuits.pop(session.key, None)
            should_clear_persisted = session.key in self._http_bridge_retry_circuit_persisted_keys
            if should_clear_persisted:
                try:
                    await self._durable_bridge.clear_retry_circuit(
                        session_key_kind=session.key.affinity_kind,
                        session_key_value=session.key.affinity_key,
                        api_key_id=session.key.api_key_id,
                    )
                    self._http_bridge_retry_circuit_loaded_keys.discard(session.key)
                    self._http_bridge_retry_circuit_persisted_keys.discard(session.key)
                except Exception:
                    logger.warning(
                        "Failed to clear persisted HTTP bridge retry circuit bridge_kind=%s bridge_key=%s",
                        session.key.affinity_kind,
                        _hash_identifier(session.key.affinity_key),
                        exc_info=True,
                    )
        if state is None:
            return
        if PROMETHEUS_AVAILABLE and http_bridge_retry_circuit_total is not None:
            http_bridge_retry_circuit_total.labels(outcome="reset").inc()
        logger.info(
            "http_bridge_retry_circuit event=reset bridge_kind=%s bridge_key=%s failures=%s",
            session.key.affinity_kind,
            _hash_identifier(session.key.affinity_key),
            state.consecutive_failures,
        )
