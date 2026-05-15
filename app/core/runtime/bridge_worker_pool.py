from __future__ import annotations

import asyncio
import os
import signal
import ssl
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from types import FrameType
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp import WSMsgType, web

from app.core.config.settings import get_settings

if TYPE_CHECKING:
    from app.cli import RuntimeOptions


_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
_PROXY_PATH_PREFIXES = ("/backend-api/", "/v1/")
_FOLLOWER_DISABLED_SETTINGS = {
    "CODEX_LB_METRICS_ENABLED": "false",
    "CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED": "false",
    "CODEX_LB_USAGE_REFRESH_ENABLED": "false",
}
_DOWNSTREAM_DISCONNECT_ERRORS = (aiohttp.ClientConnectionResetError, ConnectionResetError, BrokenPipeError)


@dataclass(frozen=True)
class BridgeWorkerConfig:
    index: int
    instance_id: str
    port: int
    base_url: str


def run_bridge_worker_pool(options: RuntimeOptions, *, log_config: dict[str, object] | None = None) -> None:
    del log_config
    raise SystemExit(asyncio.run(BridgeWorkerPool(options).serve()))


def build_worker_configs(
    *,
    worker_count: int,
    public_port: int,
    instance_id_base: str,
    base_port: int | None = None,
) -> tuple[BridgeWorkerConfig, ...]:
    if worker_count <= 1:
        raise ValueError("worker_count must be greater than one")
    resolved_base_port = base_port or int(os.getenv("CODEX_LB_BRIDGE_WORKER_BASE_PORT", str(public_port + 1000)))
    return tuple(
        BridgeWorkerConfig(
            index=index,
            instance_id=f"{instance_id_base}-worker-{index + 1}",
            port=resolved_base_port + index,
            base_url=f"http://127.0.0.1:{resolved_base_port + index}",
        )
        for index in range(worker_count)
    )


class BridgeWorkerPool:
    def __init__(self, options: RuntimeOptions) -> None:
        self._options = options
        settings = get_settings()
        self._workers = build_worker_configs(
            worker_count=options.workers,
            public_port=options.port,
            instance_id_base=settings.http_responses_session_bridge_instance_id,
        )
        self._processes: list[asyncio.subprocess.Process] = []
        self._proxy = _BridgeFrontProxy(self._workers)

    async def serve(self) -> int:
        migration_code = await self._run_startup_migration_if_needed()
        if migration_code != 0:
            return migration_code
        await self._start_workers()
        try:
            await self._wait_for_workers()
            runner = await self._proxy.start(
                host=self._options.host,
                port=self._options.port,
                ssl_context=_ssl_context(self._options),
            )
            try:
                return await self._wait_for_stop_or_worker_exit()
            finally:
                await runner.cleanup()
        finally:
            await self._stop_workers()

    async def _run_startup_migration_if_needed(self) -> int:
        if not get_settings().database_migrate_on_startup:
            return 0
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.db.migrate", "upgrade")
        return await process.wait()

    async def _start_workers(self) -> None:
        base_env = os.environ.copy()
        base_env["CODEX_LB_DATABASE_MIGRATE_ON_STARTUP"] = "false"
        base_env["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_WORKER_POOL_MODE"] = "true"
        base_env["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING"] = ""
        for worker in self._workers:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.cli",
                "--host",
                "127.0.0.1",
                "--port",
                str(worker.port),
                "--workers",
                "1",
                "--loop",
                self._options.loop,
                "--http",
                self._options.http,
                env=build_worker_env(worker, base_env=base_env),
            )
            self._processes.append(process)

    async def _wait_for_workers(self) -> None:
        timeout_seconds = max(get_settings().upstream_connect_timeout_seconds, 60.0)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0), trust_env=False) as session:
            for worker in self._workers:
                while True:
                    if any(process.returncode not in (None, 0) for process in self._processes):
                        raise RuntimeError("A bridge worker exited before startup completed")
                    try:
                        async with session.get(f"{worker.base_url}/health/ready") as response:
                            if response.status == 200:
                                break
                    except aiohttp.ClientError:
                        pass
                    if asyncio.get_running_loop().time() >= deadline:
                        raise RuntimeError(f"Bridge worker did not become ready: {worker.instance_id}")
                    await asyncio.sleep(0.2)

    async def _wait_for_stop_or_worker_exit(self) -> int:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _stop(_: int, __: FrameType | None = None) -> None:
            stop_event.set()

        previous_handlers: dict[int, object] = {}
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[sig] = signal.getsignal(sig)
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                signal.signal(sig, _stop)

        worker_waits = [asyncio.create_task(process.wait()) for process in self._processes]
        stop_wait = asyncio.create_task(stop_event.wait())
        try:
            done, pending = await asyncio.wait([stop_wait, *worker_waits], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if stop_wait in done:
                return 0
            for task in done:
                result = task.result()
                if result != 0:
                    return int(result)
            return 1
        finally:
            for sig, handler in previous_handlers.items():
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError):
                    if handler is not None:
                        signal.signal(sig, handler)

    async def _stop_workers(self) -> None:
        for process in self._processes:
            if process.returncode is None:
                process.terminate()
        await asyncio.gather(*(process.wait() for process in self._processes), return_exceptions=True)


class _BridgeFrontProxy:
    def __init__(self, workers: tuple[BridgeWorkerConfig, ...]) -> None:
        self._workers = workers
        self._counter = 0
        self._lock = asyncio.Lock()
        self._client: aiohttp.ClientSession | None = None

    async def start(self, *, host: str, port: int, ssl_context: ssl.SSLContext | None) -> web.AppRunner:
        self._client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=30.0),
            trust_env=False,
        )
        app = web.Application(
            client_max_size=128 * 1024 * 1024,
            handler_args={"auto_decompress": False},
        )
        app.on_cleanup.append(self._close)
        app.router.add_route("*", "/{tail:.*}", self._handle)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host=host, port=port, ssl_context=ssl_context).start()
        return runner

    async def _close(self, _: web.Application) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        worker = await self._select_worker(request.path)
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await self._handle_websocket(request, worker)
        return await self._handle_http(request, worker)

    async def _select_worker(self, path: str) -> BridgeWorkerConfig:
        if not path.startswith(_PROXY_PATH_PREFIXES):
            return self._workers[0]
        async with self._lock:
            worker = self._workers[self._counter % len(self._workers)]
            self._counter += 1
            return worker

    async def _handle_http(self, request: web.Request, worker: BridgeWorkerConfig) -> web.StreamResponse:
        client = self._required_client()
        target_url = _target_url(worker, request)
        data = request.content.iter_chunked(64 * 1024) if request.can_read_body else None
        async with client.request(
            request.method,
            target_url,
            headers=_forward_request_headers(request.headers),
            data=data,
            allow_redirects=False,
        ) as upstream:
            response = web.StreamResponse(
                status=upstream.status,
                reason=upstream.reason,
                headers=_forward_response_headers(upstream.headers),
            )
            await response.prepare(request)
            try:
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    await response.write(chunk)
                await response.write_eof()
            except _DOWNSTREAM_DISCONNECT_ERRORS:
                return response
            return response

    async def _handle_websocket(self, request: web.Request, worker: BridgeWorkerConfig) -> web.StreamResponse:
        client = self._required_client()
        downstream = web.WebSocketResponse()
        await downstream.prepare(request)
        async with client.ws_connect(
            _target_url(worker, request, websocket=True),
            headers=_forward_request_headers(request.headers),
            max_msg_size=0,
        ) as upstream:
            await _relay_websockets(downstream, upstream)
        return downstream

    def _required_client(self) -> aiohttp.ClientSession:
        if self._client is None:
            raise RuntimeError("bridge front proxy is not started")
        return self._client


async def _relay_websockets(downstream: web.WebSocketResponse, upstream: aiohttp.ClientWebSocketResponse) -> None:
    async def _client_to_upstream() -> None:
        async for message in downstream:
            if message.type == WSMsgType.TEXT:
                await upstream.send_str(message.data)
            elif message.type == WSMsgType.BINARY:
                await upstream.send_bytes(message.data)
            elif message.type == WSMsgType.CLOSE:
                await upstream.close(code=downstream.close_code or 1000)

    async def _upstream_to_client() -> None:
        async for message in upstream:
            if message.type == WSMsgType.TEXT:
                await downstream.send_str(message.data)
            elif message.type == WSMsgType.BINARY:
                await downstream.send_bytes(message.data)
            elif message.type == WSMsgType.CLOSE:
                await downstream.close(code=upstream.close_code or 1000)

    tasks = {asyncio.create_task(_client_to_upstream()), asyncio.create_task(_upstream_to_client())}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


def _target_url(worker: BridgeWorkerConfig, request: web.Request, *, websocket: bool = False) -> str:
    parsed = urlsplit(worker.base_url)
    scheme = "ws" if websocket else parsed.scheme
    return urlunsplit((scheme, parsed.netloc, request.path, request.query_string, ""))


def _forward_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in _HOP_BY_HOP_HEADERS}


def _forward_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in _HOP_BY_HOP_HEADERS}


def _is_downstream_disconnect(exc: BaseException) -> bool:
    return isinstance(exc, _DOWNSTREAM_DISCONNECT_ERRORS)


def build_worker_env(worker: BridgeWorkerConfig, *, base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env["PORT"] = str(worker.port)
    env["CODEX_LB_UVICORN_WORKERS"] = "1"
    env["UVICORN_WORKERS"] = "1"
    env["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID"] = worker.instance_id
    env["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL"] = worker.base_url
    if worker.index > 0:
        env.update(_FOLLOWER_DISABLED_SETTINGS)
    return env


def _ssl_context(options: RuntimeOptions) -> ssl.SSLContext | None:
    if not options.ssl_certfile or not options.ssl_keyfile:
        return None
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(options.ssl_certfile, options.ssl_keyfile)
    return context
