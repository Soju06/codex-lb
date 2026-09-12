"""Bounded stages, private reports and owned process groups for local verification."""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class VerificationError(Exception):
    """A safe, fixed diagnostic; never embed an upstream payload here."""


@dataclass
class Stage:
    name: str
    status: str = "running"
    elapsed_seconds: float = 0
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Report:
    def __init__(self, path: Path | None, timeout: float, heartbeat: float = 10):
        if not math.isfinite(timeout) or timeout <= 0 or heartbeat <= 0 or not math.isfinite(heartbeat):
            raise ValueError("Deadlines and heartbeat must be finite and positive")
        self.path, self.timeout, self.heartbeat = path, timeout, heartbeat
        self.stages: list[Stage] = []
        self.status = "running"
        self.started_at = datetime.now(UTC).isoformat()
        if path is not None:
            with path.open("x"):
                pass
            path.chmod(0o600)
        self.write()

    def write(self) -> None:
        if self.path is not None:
            pending = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
            try:
                with pending.open("x") as handle:
                    pending.chmod(0o600)
                    json.dump(
                        {
                            "status": self.status,
                            "startedAt": self.started_at,
                            "stages": [asdict(s) for s in self.stages],
                        },
                        handle,
                        indent=2,
                    )
                pending.replace(self.path)
            finally:
                pending.unlink(missing_ok=True)

    def finish(self, status: str) -> None:
        if status not in {"passed", "failed", "cancelled"}:
            raise ValueError("Invalid final verification status")
        if status == "passed" and (not self.stages or any(stage.status != "passed" for stage in self.stages)):
            raise VerificationError("Cannot pass an incomplete or failed verification")
        self.status = status
        self.write()
        print(json.dumps({"verification": status}), flush=True)

    async def run[T](
        self,
        name: str,
        operation: Callable[[], Awaitable[T]],
        evidence: Callable[[T], dict[str, Any]] | None = None,
        *,
        initial: dict[str, Any] | None = None,
    ) -> T:
        stage = Stage(name, details=initial or {})
        self.stages.append(stage)
        started = time.monotonic()

        def emit() -> None:
            stage.elapsed_seconds = round(time.monotonic() - started, 3)
            self.write()
            print(json.dumps(asdict(stage)), flush=True)

        async def progress() -> None:
            while True:
                await asyncio.sleep(self.heartbeat)
                emit()

        emit()
        ticker = asyncio.create_task(progress())
        try:
            async with asyncio.timeout(self.timeout):
                value = await operation()
            stage.details.update(evidence(value) if evidence else {})
            stage.status = "passed"
            return value
        except BaseException as exc:
            stage.status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            self.status = stage.status
            stage.error = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
            raise
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            emit()


async def child(command: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> int:
    with log.open("xb") as output:
        log.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            *command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            return await process.wait()
        finally:
            # Own the entire group: a pytest/plugin child must not survive a timeout.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(3):
                    await process.wait()
            except TimeoutError:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
