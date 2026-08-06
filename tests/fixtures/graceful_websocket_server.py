from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

import uvicorn

from app.core import shutdown as shutdown_state
from app.core.middleware.inflight import InFlightMiddleware
from app.core.server import GracefulDrainServer
from app.core.shutdown import DRAIN_DEADLINE_HEADER


class _LifecycleApp:
    def __init__(
        self,
        *,
        mode: str,
        completion_delay_seconds: float,
        drain_timeout_seconds: float,
    ) -> None:
        self._mode = mode
        self._completion_delay_seconds = completion_delay_seconds
        self._drain_timeout_seconds = drain_timeout_seconds

    async def _lifespan(self, receive, send) -> None:  # noqa: ANN001
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
                continue
            if message["type"] != "lifespan.shutdown":
                continue
            if self._mode != "cleanup_stuck":
                await send({"type": "lifespan.shutdown.complete"})
                return
            while True:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # Reproduce an application cleanup that absorbs task
                    # cancellation. asyncio.run() would otherwise gather this
                    # task without a bound during runner teardown.
                    continue

    @staticmethod
    async def _send_http_json(send, *, status: int, payload: object) -> None:  # noqa: ANN001
        body = json.dumps(payload, separators=(",", ":")).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return

        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "GET")
            if path == "/internal/drain/start" and method == "POST":
                headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
                deadline_value = headers.get(DRAIN_DEADLINE_HEADER)
                deadline = float(deadline_value) if deadline_value is not None else None
                if deadline is None:
                    effective_deadline = shutdown_state.begin_drain(self._drain_timeout_seconds)
                else:
                    effective_deadline = shutdown_state.commit_shutdown(
                        self._drain_timeout_seconds,
                        deadline_monotonic=deadline,
                    )
                await self._send_http_json(
                    send,
                    status=200,
                    payload={
                        "status": "ok",
                        "checks": {
                            "draining": str(shutdown_state.is_draining()).lower(),
                            "shutdown_committed": str(shutdown_state.is_shutdown_committed()).lower(),
                            "deadline_monotonic": format(effective_deadline, ".17g"),
                        },
                    },
                )
                return
            if path == "/internal/drain/stop" and method == "POST":
                stopped = shutdown_state.stop_drain()
                await self._send_http_json(
                    send,
                    status=200 if stopped else 409,
                    payload={
                        "status": "ok" if stopped else "conflict",
                        "checks": {"draining": str(shutdown_state.is_draining()).lower()},
                    },
                )
                return
            await self._send_http_json(
                send,
                status=200,
                payload={
                    "status": "ok",
                    "checks": {
                        "draining": str(shutdown_state.is_draining()).lower(),
                        "shutdown_committed": str(shutdown_state.is_shutdown_committed()).lower(),
                        "in_flight": str(shutdown_state.get_in_flight()),
                    },
                },
            )
            return

        if scope["type"] != "websocket":
            return

        connect_message = await receive()
        assert connect_message["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        request_message = await receive()
        assert request_message["type"] == "websocket.receive"
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_process_drain", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            }
        )

        if self._mode in {"stuck", "cleanup_stuck"}:
            await asyncio.Event().wait()
            return

        while not shutdown_state.is_draining():
            await asyncio.sleep(0.01)
        await asyncio.sleep(self._completion_delay_seconds)
        await send(
            {
                "type": "websocket.send",
                "text": json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_process_drain",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            }
        )
        await send({"type": "websocket.close", "code": 1012, "reason": "Server is draining"})


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--drain-timeout-seconds", required=True, type=float)
    parser.add_argument("--mode", choices=("complete", "stuck", "cleanup_stuck"), required=True)
    parser.add_argument("--completion-delay-seconds", type=float, default=0.0)
    parser.add_argument("--post-drain-cleanup-timeout-seconds", type=float, default=25.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    shutdown_state.reset()
    app = InFlightMiddleware(
        _LifecycleApp(
            mode=args.mode,
            completion_delay_seconds=args.completion_delay_seconds,
            drain_timeout_seconds=args.drain_timeout_seconds,
        )
    )
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=args.port,
        lifespan="on" if args.mode == "cleanup_stuck" else "off",
        log_level="warning",
        timeout_graceful_shutdown=args.drain_timeout_seconds,
    )
    server = GracefulDrainServer(
        config,
        drain_timeout_seconds=args.drain_timeout_seconds,
        post_drain_cleanup_timeout_seconds=args.post_drain_cleanup_timeout_seconds,
    )
    server.run()
    return 0 if server.started else 3


if __name__ == "__main__":
    raise SystemExit(main())
