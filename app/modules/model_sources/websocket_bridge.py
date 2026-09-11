"""Adapt company WebSocket turns to the policy-owning HTTP ASGI route."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import cast

import anyio
from sqlalchemy import select
from starlette.types import Message
from starlette.websockets import WebSocket

from app.core.config.settings import get_settings
from app.core.types import JsonValue
from app.db.models import ModelSource, ModelSourceModel
from app.db.session import get_background_session
from app.modules.model_sources.governance import COMPANY_KINDS


class CompanyWebSocketBridge:
    def __init__(self, websocket: WebSocket, send_lock: anyio.Lock) -> None:
        self.websocket = websocket
        self.send_lock = send_lock
        self.pending: deque[Message] = deque()
        self.anchor: str | None = None
        self.model: str | None = None
        self.history: list[JsonValue] = []
        self.source_id: str | None = None
        self.disconnected = False
        self.company_models: set[str] = set()

    async def load_models(self) -> None:
        if not isinstance(self.websocket, WebSocket) or "app" not in self.websocket.scope:
            return
        async with get_background_session() as session:
            self.company_models = set(
                await session.scalars(
                    select(ModelSourceModel.model)
                    .join(ModelSource)
                    .where(
                        ModelSource.kind.in_(COMPANY_KINDS),
                    )
                )
            )

    async def receive(self) -> Message:
        if self.pending:
            return self.pending.popleft()
        return await self.websocket.receive()

    async def emit(self, event: dict[str, JsonValue]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(event)

    async def error(self, code: str, message: str, status: int = 400) -> None:
        await self.emit(
            {
                "type": "error",
                "status": status,
                "error": {
                    "type": "invalid_request_error" if status < 500 else "server_error",
                    "code": code,
                    "message": message,
                },
            }
        )

    async def handle(self, payload: dict[str, JsonValue]) -> bool:
        if not isinstance(self.websocket, WebSocket):
            return False
        model = payload.get("model")
        if not isinstance(model, str) or "app" not in self.websocket.scope:
            return False
        if model not in self.company_models:
            return False
        async with get_background_session() as session:
            owned = await session.scalar(
                select(ModelSource.id)
                .join(ModelSourceModel)
                .where(
                    ModelSource.kind.in_(COMPANY_KINDS),
                    ModelSourceModel.model == model,
                )
                .limit(1)
            )
        if owned is None:
            return False
        limit = get_settings().max_sse_event_bytes
        body = dict(payload)
        body.pop("type", None)
        if body.pop("generate", True) is False:
            await self.error("unsupported_warmup", "Company sources do not support non-generating warmup.")
            return True
        previous = body.pop("previous_response_id", None)
        incoming = body.get("input", [])
        if isinstance(incoming, str):
            incoming = [{"role": "user", "content": incoming}]
        if not isinstance(incoming, list):
            await self.error("invalid_request", "Response input must be text or an array.")
            return True
        if previous is not None:
            if previous != self.anchor or model != self.model or owned != self.source_id:
                await self.error(
                    "previous_response_not_found", "Company response context is unavailable; resend full input."
                )
                return True
            incoming = [*self.history, *incoming]
        body["input"] = cast(list[JsonValue], incoming)
        body["stream"] = True
        encoded = json.dumps(body).encode()
        if len(encoded) > limit:
            await self.error("request_too_large", "Company response context exceeds the frame limit.", 413)
            return True
        scope = dict(self.websocket.scope)
        scope["company_websocket_source_id"] = owned
        scope.update(
            type="http", method="POST", http_version="1.1", scheme="http", query_string=b"", company_websocket=True
        )
        scope["headers"] = [
            (k, v)
            for k, v in scope["headers"]
            if k.lower()
            not in {
                b"connection",
                b"upgrade",
                b"sec-websocket-key",
                b"sec-websocket-version",
                b"sec-websocket-extensions",
                b"content-length",
                b"content-type",
            }
        ] + [(b"content-type", b"application/json"), (b"content-length", str(len(encoded)).encode())]
        scope["state"] = dict(scope.get("state", {}))
        delivered = False
        disconnect = asyncio.Event()
        status = 200
        buffer = b""
        completed: dict[str, JsonValue] | None = None

        async def receive_http() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": encoded, "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send_http(message: Message) -> None:
            nonlocal status, buffer, completed
            if message["type"] == "http.response.start":
                status = message["status"]
                return
            if message["type"] != "http.response.body":
                return
            buffer += message.get("body", b"")
            if len(buffer) > limit:
                raise ValueError("Company response frame exceeds limit")
            if status >= 400:
                if not message.get("more_body", False):
                    envelope = json.loads(buffer)
                    await self.emit({"type": "error", "status": status, "error": envelope.get("error", envelope)})
                return
            buffer = buffer.replace(b"\r\n", b"\n")
            while b"\n\n" in buffer:
                frame, buffer = buffer.split(b"\n\n", 1)
                data = b"\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith(b"data:"))
                if not data or data == b"[DONE]":
                    continue
                event = json.loads(data)
                if event.get("type") == "response.completed" and isinstance(event.get("response"), dict):
                    completed = event["response"]
                await self.emit(event)

        disconnected = False
        cancelled = False

        async def watch_client() -> None:
            nonlocal disconnected, cancelled
            while True:
                message = await self.websocket.receive()
                if message["type"] == "websocket.disconnect":
                    disconnected = True
                    disconnect.set()
                    return
                try:
                    event = json.loads(message.get("text", ""))
                except (ValueError, TypeError):
                    event = None
                if isinstance(event, dict) and event.get("type") == "response.cancel":
                    cancelled = True
                    disconnect.set()
                    return
                if len(self.pending) >= 1:
                    await self.error("request_in_progress", "Only one queued company request is allowed.", 429)
                else:
                    self.pending.append(message)

        app_task = asyncio.create_task(self.websocket.scope["app"](scope, receive_http, send_http))
        watcher = asyncio.create_task(watch_client())
        try:
            done, _ = await asyncio.wait((app_task, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
                app_task.cancel()
            await asyncio.gather(app_task, return_exceptions=False)
        except asyncio.CancelledError:
            if not disconnected and not cancelled:
                raise
        finally:
            disconnect.set()
            app_task.cancel()
            watcher.cancel()
            await asyncio.gather(app_task, watcher, return_exceptions=True)
        if disconnected:
            self.disconnected = True
            return True
        if cancelled:
            await self.error("response_cancelled", "Response cancelled by client.")
        elif completed is not None and isinstance(completed.get("output"), list):
            history = [*incoming, *completed["output"]]
            if len(json.dumps(history).encode()) <= limit:
                self.anchor = str(completed["id"])
                self.model = model
                self.source_id = owned
                self.history = history
            else:
                self.anchor, self.model, self.history = None, None, []
        return True
