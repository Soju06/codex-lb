"""Credential-isolated native Responses WebSocket transport for model sources."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus

from app.core.config.settings import get_settings
from app.core.types import JsonValue
from app.core.utils.proxy_env import resolve_websocket_proxy_from_env
from app.db.models import ModelSource
from app.modules.model_sources.forwarding import ModelSourceForwardingError, _source_api_key_secret

MAX_MESSAGE_BYTES = 16 * 1024 * 1024


def source_websocket_url(source: ModelSource) -> str:
    parsed = urlsplit(source.base_url.rstrip("/"))
    scheme = {"https": "wss", "http": "ws"}.get(parsed.scheme)
    if scheme is None or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Invalid model source endpoint")
    return urlunsplit((scheme, parsed.netloc, f"{parsed.path.rstrip('/')}/responses", parsed.query, ""))


class _SourceConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        # A source handshake is bound to its configured endpoint, including
        # same-origin redirects. Never replay source credentials on a redirect.
        return exc


class _SourceConnection(ClientConnection):
    async def keepalive(self) -> None:
        """Send probes without retaining a future for each unanswered pong."""
        assert self.ping_interval is not None
        try:
            while True:
                await asyncio.sleep(self.ping_interval)
                # The base connection owns this task and cancels it on close.
                # Send through its flow-controlled context, but don't create
                # acknowledgment futures: source liveness uses response budgets.
                async with self.send_context():
                    self.protocol.send_ping(b"")
        except Exception:
            # Provider close reasons may contain secrets; don't log exceptions.
            self.logger.warning("source_websocket_keepalive_failed")


def source_ws_error(code: str, message: str, *, status: int = 400) -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=status,
        payload={
            "error": {
                "type": "invalid_request_error" if status < 500 else "server_error",
                "code": code,
                "message": message,
            }
        },
    )


def parse_source_event(value: str | bytes) -> dict[str, JsonValue] | None:
    if not isinstance(value, str) or len(value.encode("utf-8", errors="replace")) > MAX_MESSAGE_BYTES:
        return None
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError):
        return None
    return decoded if isinstance(decoded, dict) else None


async def open_source_websocket(source: ModelSource, *, timeout: float) -> ClientConnection:
    try:
        url = source_websocket_url(source)
        secret = _source_api_key_secret(source, encryptor=None)
        proxy = resolve_websocket_proxy_from_env(url) if get_settings().upstream_websocket_trust_env else None
        return await _SourceConnect(
            url,
            additional_headers={"Authorization": f"Bearer {secret}"} if secret else None,
            proxy=proxy,
            open_timeout=timeout,
            # Source providers can defer control-frame handling while a long
            # Responses generation is in flight. Keep sending pings, but let
            # the response first-frame/idle/turn budgets decide whether work
            # is stalled instead of closing a progressing stream after the
            # library's short default pong deadline.
            ping_timeout=None,
            create_connection=_SourceConnection,
            close_timeout=2,
            max_size=MAX_MESSAGE_BYTES,
            max_queue=4,
            compression=None,
        )
    except InvalidStatus as exc:
        # Neither the handshake body nor the provider's reason text is trusted:
        # both can contain echoed credentials, URLs or provider aliases.
        error = source_ws_error(
            "model_source_websocket_handshake_failed", "Model source rejected the WebSocket handshake", status=502
        )
        error.upstream_status_code = exc.response.status_code
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after and len(retry_after) <= 128:
            error.retry_after = retry_after
        raise error from None
    except (OSError, TimeoutError):
        error = source_ws_error(
            "model_source_websocket_connect_failed", "Unable to connect to model source WebSocket", status=502
        )
        error.connection_failed = True
        raise error from None
    except ModelSourceForwardingError:
        raise
    except Exception:
        raise source_ws_error(
            "model_source_websocket_connect_failed", "Unable to connect to model source WebSocket", status=502
        ) from None
