from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import aiohttp

from app.core.config.settings import get_settings
from app.db.session import get_background_session
from app.modules.telemetry.consent import TelemetryConsentStore, TelemetryIdentity
from app.modules.telemetry.schemas import (
    TelemetryActivation,
    TelemetryModel,
    TelemetryRegistration,
    TelemetrySnapshot,
    build_snapshot_envelope,
)

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5.0
_MAX_ATTEMPTS = 2
SenderContextProvider = Callable[[], Awaitable[tuple[bool, TelemetryIdentity | None]]]


class TelemetryProtocolError(RuntimeError):
    pass


class TelemetrySender:
    def __init__(
        self,
        endpoint: str | None = None,
        *,
        context_provider: SenderContextProvider | None = None,
    ) -> None:
        self._endpoint = (endpoint or get_settings().telemetry_endpoint).rstrip("/")
        self._context_provider = context_provider or _load_sender_context
        self._activated_instance_id: str | None = None

    async def send_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        try:
            active, identity = await self._context_provider()
            if not active:
                return
            if identity is None:
                raise TelemetryProtocolError("active telemetry has no identity")
            if snapshot.instance_id != identity.instance_id:
                raise TelemetryProtocolError("snapshot identity does not match persisted telemetry identity")
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
                async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                    await self._send_with_retry(session, snapshot, identity)
        except Exception as exc:
            logger.debug("Anonymous telemetry transmission failed", exc_info=exc)

    async def _send_with_retry(
        self,
        session: aiohttp.ClientSession,
        snapshot: TelemetrySnapshot,
        identity: TelemetryIdentity,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                await self._transmit_once(session, snapshot, identity)
                return
            except Exception as exc:
                last_error = exc
                logger.debug("Anonymous telemetry attempt %d failed", attempt + 1, exc_info=exc)
        if last_error is not None:
            raise last_error

    async def _transmit_once(
        self,
        session: aiohttp.ClientSession,
        snapshot: TelemetrySnapshot,
        identity: TelemetryIdentity,
    ) -> None:
        if self._activated_instance_id != identity.instance_id:
            registration = TelemetryRegistration(
                app_version=snapshot.version,
                deployment_mode=snapshot.deploy.method,
                instance_id=identity.instance_id,
                os_arch=f"{snapshot.os}/{snapshot.arch}",
                public_key=identity.public_key_hex,
            )
            await self._post(session, "/v1/register", _json_bytes(registration), accepted={200, 201})

            activation = TelemetryActivation()
            await self._post_signed(session, "/v1/activate", _json_bytes(activation), identity, accepted={200})
            self._activated_instance_id = identity.instance_id

        envelope = build_snapshot_envelope(snapshot)
        await self._post_signed(session, "/v1/snapshot", _json_bytes(envelope), identity, accepted={200, 202})

    async def _post_signed(
        self,
        session: aiohttp.ClientSession,
        path: str,
        body: bytes,
        identity: TelemetryIdentity,
        *,
        accepted: set[int],
    ) -> None:
        await self._post(
            session,
            path,
            body,
            accepted=accepted,
            headers={
                "X-Instance-ID": identity.instance_id,
                "X-Signature": identity.private_key.sign(body).hex(),
            },
        )

    async def _post(
        self,
        session: aiohttp.ClientSession,
        path: str,
        body: bytes,
        *,
        accepted: set[int],
        headers: dict[str, str] | None = None,
    ) -> None:
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        async with session.post(f"{self._endpoint}{path}", data=body, headers=request_headers) as response:
            await response.read()
            if response.status not in accepted:
                raise TelemetryProtocolError(f"SHM {path} returned HTTP {response.status}")


async def _load_sender_context() -> tuple[bool, TelemetryIdentity | None]:
    async with get_background_session() as session:
        store = TelemetryConsentStore(session)
        consent = await store.resolve()
        if not consent.active:
            return False, None
        return True, await store.get_or_create_identity()


def _json_bytes(value: TelemetryModel) -> bytes:
    return json.dumps(value.model_dump(mode="json"), separators=(",", ":"), sort_keys=True).encode("utf-8")
