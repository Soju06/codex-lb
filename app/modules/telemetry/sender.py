from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import aiohttp

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.telemetry.consent import TelemetryConsentStore, TelemetryIdentity
from app.modules.telemetry.schemas import (
    DeploymentMethod,
    TelemetryActivation,
    TelemetryModel,
    TelemetryOptOut,
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
                    await self._send_with_retry(lambda: self._transmit_once(session, snapshot, identity))
        except Exception as exc:
            logger.debug("Anonymous telemetry transmission failed", exc_info=exc)

    async def send_opt_out(
        self,
        identity: TelemetryIdentity,
        *,
        app_version: str,
        deployment_mode: DeploymentMethod,
        os_arch: str,
    ) -> None:
        try:
            event = TelemetryOptOut(
                app_version=app_version,
                instance_id=identity.instance_id,
                occurred_at=f"{utcnow().isoformat()}Z",
            )
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
                async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                    await self._send_with_retry(
                        lambda: self._transmit_opt_out_once(
                            session,
                            event,
                            identity,
                            deployment_mode=deployment_mode,
                            os_arch=os_arch,
                        )
                    )
        except Exception as exc:
            logger.debug("Anonymous telemetry opt-out transmission failed", exc_info=exc)

    async def _send_with_retry(self, operation: Callable[[], Awaitable[None]]) -> None:
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                await operation()
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
        await self._ensure_activated(
            session,
            identity,
            app_version=snapshot.version,
            deployment_mode=snapshot.deploy.method,
            os_arch=f"{snapshot.os}/{snapshot.arch}",
        )

        envelope = build_snapshot_envelope(snapshot)
        try:
            active, current_identity = await self._context_provider()
            identity_matches = (
                current_identity is not None
                and current_identity.instance_id == identity.instance_id
                and current_identity.public_key_hex == identity.public_key_hex
            )
        except Exception as exc:
            logger.debug("Anonymous telemetry consent re-check failed", exc_info=exc)
            return
        if not active or not identity_matches:
            return

        await self._post_signed(session, "/v1/snapshot", _json_bytes(envelope), identity, accepted={200, 202})

    async def _transmit_opt_out_once(
        self,
        session: aiohttp.ClientSession,
        event: TelemetryOptOut,
        identity: TelemetryIdentity,
        *,
        deployment_mode: DeploymentMethod,
        os_arch: str,
    ) -> None:
        await self._ensure_activated(
            session,
            identity,
            app_version=event.app_version,
            deployment_mode=deployment_mode,
            os_arch=os_arch,
        )
        await self._post_signed(session, "/v1/optout", _json_bytes(event), identity, accepted={200})

    async def _ensure_activated(
        self,
        session: aiohttp.ClientSession,
        identity: TelemetryIdentity,
        *,
        app_version: str,
        deployment_mode: DeploymentMethod,
        os_arch: str,
    ) -> None:
        if self._activated_instance_id == identity.instance_id:
            return
        registration = TelemetryRegistration(
            app_version=app_version,
            deployment_mode=deployment_mode,
            instance_id=identity.instance_id,
            os_arch=os_arch,
            public_key=identity.public_key_hex,
        )
        await self._post(session, "/v1/register", _json_bytes(registration), accepted={200, 201})

        activation = TelemetryActivation()
        await self._post_signed(session, "/v1/activate", _json_bytes(activation), identity, accepted={200})
        self._activated_instance_id = identity.instance_id

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
