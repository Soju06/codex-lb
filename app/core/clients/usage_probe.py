from __future__ import annotations

import asyncio

import aiohttp

from app.core.clients.codex import CodexClient, CodexTransportError, create_codex_session
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.upstream_proxy import ResolvedUpstreamRoute

DEFAULT_PROBE_MODEL = "gpt-5.5"


async def send_usage_probe(
    *,
    access_token: str,
    chatgpt_account_id: str | None,
    route: ResolvedUpstreamRoute | None,
    model: str = DEFAULT_PROBE_MODEL,
) -> int:
    """Wake one account's limiter without selection or cross-account fallback."""
    base = get_settings().upstream_base_url.rstrip("/")
    if "/backend-api" not in base:
        base = f"{base}/backend-api"
    url = f"{base}/codex/responses"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if chatgpt_account_id and not chatgpt_account_id.startswith(("email_", "local_")):
        headers["chatgpt-account-id"] = chatgpt_account_id
    body = {
        "model": model,
        "instructions": "Respond with a single dot.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "."}]}],
        "max_output_tokens": 1,
        "stream": True,
        "store": False,
    }
    timeout = aiohttp.ClientTimeout(total=30.0, sock_connect=10.0)
    try:
        if route is not None:
            client = CodexClient(create_codex_session())
            try:
                response = await client.request(
                    "POST",
                    url,
                    route=route,
                    headers=headers,
                    json=body,
                    timeout=timeout,
                )
                return response.status
            finally:
                await client.close()
        async with lease_http_session() as session:
            async with session.post(url, headers=headers, json=body, timeout=timeout) as response:
                return response.status
    except (aiohttp.ClientError, asyncio.TimeoutError, CodexTransportError):
        # Transport exceptions may contain proxy credentials; never log them.
        return 0
