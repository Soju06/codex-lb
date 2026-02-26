from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from app.core.openai.requests import ResponsesRequest
from app.db.session import get_background_session
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData, ApiKeysService, ApiKeyUsageReservationData
from app.modules.proxy.service import ProxyService


class AnthropicCompatService:
    def __init__(self, proxy_service: ProxyService) -> None:
        self._proxy_service = proxy_service

    def stream_responses(
        self,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        suppress_text_done_events: bool = False,
    ) -> AsyncIterator[str]:
        payload.stream = True
        return self._proxy_service.stream_responses(
            payload,
            headers,
            propagate_http_errors=True,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            suppress_text_done_events=suppress_text_done_events,
        )

    async def rate_limit_headers(self) -> dict[str, str]:
        return await self._proxy_service.rate_limit_headers()

    async def enforce_request_limits(
        self,
        api_key: ApiKeyData | None,
        *,
        request_model: str | None,
    ) -> ApiKeyUsageReservationData | None:
        if api_key is None:
            return None

        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            return await service.enforce_limits_for_request(
                api_key.id,
                request_model=request_model,
            )

    async def release_usage_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None:
            return

        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            await service.release_usage_reservation(reservation.reservation_id)
