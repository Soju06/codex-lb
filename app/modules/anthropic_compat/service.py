from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData
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
