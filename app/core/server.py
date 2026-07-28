from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from typing import NoReturn

import uvicorn

from app.core import shutdown as shutdown_state

logger = logging.getLogger("uvicorn.error")

POST_DRAIN_CLEANUP_TIMEOUT_SECONDS = 25.0


def _force_process_exit(signum: int) -> NoReturn:
    try:
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)
    finally:
        os._exit(128 + signum)


def _finish_abandoned_uvicorn_shutdown(
    task: asyncio.Task[None],
    *,
    config: uvicorn.Config,
    original_timeout: float | None,
) -> None:
    setattr(config, "timeout_graceful_shutdown", original_timeout)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Abandoned Uvicorn shutdown finished with error", exc_info=exc)


class SignalNeutralServer(uvicorn.Server):
    """Embedded server that leaves process signal ownership to its parent."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class GracefulDrainServer(uvicorn.Server):
    """Uvicorn server that drains application work before closing connections."""

    def __init__(
        self,
        config: uvicorn.Config,
        *,
        drain_timeout_seconds: float,
        post_drain_cleanup_timeout_seconds: float = POST_DRAIN_CLEANUP_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(config)
        self._drain_timeout_seconds = max(float(drain_timeout_seconds), 0.0)
        self._post_drain_cleanup_timeout_seconds = max(float(post_drain_cleanup_timeout_seconds), 0.0)

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        # Reset process-global state before Uvicorn installs its signal
        # handlers. Lifespan startup is too late: SIGTERM may already have
        # committed the one-way barrier by then.
        shutdown_state.prepare_server_start()
        await super().serve(sockets=sockets)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        shutdown_state.commit_shutdown(timeout_seconds=self._drain_timeout_seconds)
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        shutdown_state.commit_shutdown(timeout_seconds=self._drain_timeout_seconds)
        remaining = shutdown_state.remaining_drain_timeout_seconds() or 0.0
        drained = await shutdown_state.wait_for_in_flight_drain(
            timeout_seconds=remaining,
            should_abort=lambda: self.force_exit,
        )
        if not drained and not self.force_exit:
            logger.warning("Drain timeout reached before connection shutdown")

        original_timeout = self.config.timeout_graceful_shutdown
        remaining = shutdown_state.remaining_drain_timeout_seconds() or 0.0
        bounded_timeout = remaining
        if original_timeout is not None:
            bounded_timeout = min(float(original_timeout), bounded_timeout)
        # Uvicorn annotates this setting as int but passes it directly to
        # asyncio.timeout(); preserving the float avoids discarding the last
        # subsecond of the shared process deadline.
        setattr(self.config, "timeout_graceful_shutdown", bounded_timeout)
        shutdown_wait_timeout = remaining + self._post_drain_cleanup_timeout_seconds
        shutdown_task = asyncio.create_task(
            super().shutdown(sockets=sockets),
            name="uvicorn-post-drain-shutdown",
        )
        restore_timeout_on_return = True
        try:
            done, _ = await asyncio.wait(
                {shutdown_task},
                timeout=shutdown_wait_timeout,
            )
            if shutdown_task in done:
                await shutdown_task
                return

            restore_timeout_on_return = False
            shutdown_task.cancel()
            shutdown_task.add_done_callback(
                lambda task: _finish_abandoned_uvicorn_shutdown(
                    task,
                    config=self.config,
                    original_timeout=original_timeout,
                )
            )
            logger.warning(
                "Shared drain deadline plus %.1fs Uvicorn cleanup reserve exhausted; forcing process exit",
                self._post_drain_cleanup_timeout_seconds,
            )
            exit_signal = self._captured_signals[-1] if self._captured_signals else signal.SIGTERM
            _force_process_exit(exit_signal)
        except asyncio.CancelledError:
            restore_timeout_on_return = False
            shutdown_task.cancel()
            shutdown_task.add_done_callback(
                lambda task: _finish_abandoned_uvicorn_shutdown(
                    task,
                    config=self.config,
                    original_timeout=original_timeout,
                )
            )
            raise
        finally:
            if restore_timeout_on_return:
                setattr(self.config, "timeout_graceful_shutdown", original_timeout)
