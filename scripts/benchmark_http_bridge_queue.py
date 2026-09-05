"""Compare bridge event delivery on checked-out revisions using one interpreter.

Run with --repo pointing at each revision's registered worktree. Main predates
the bounded queue and reader helper, so its read is asyncio.wait_for(queue.get()).
Timing and task-count passes are separate. This measures local queue/reader CPU,
not HTTP throughput, native transport, or production fleet capacity.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


async def measure(mode: str, count: int, submit: Any, streaming: Any, *, instrument: bool) -> dict[str, float]:
    queue_type = getattr(submit, "_HTTPBridgeLiveEventQueue", None)
    queue = queue_type(maxsize=2, revoked=asyncio.Event()) if queue_type else asyncio.Queue()
    reader = getattr(streaming, "_next_http_bridge_event_block", None)
    payload = 'data: {"type":"response.output_text.delta","delta":"benchmark"}\n\n'

    async def read() -> str:
        return await reader(queue, timeout=10.0) if reader else await asyncio.wait_for(queue.get(), timeout=10.0)

    task_count = 0
    loop = asyncio.get_running_loop()
    previous_factory = loop.get_task_factory()

    def factory(loop: asyncio.AbstractEventLoop, coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
        nonlocal task_count
        task_count += 1
        return asyncio.Task(coro, loop=loop, **kwargs)

    if instrument:
        loop.set_task_factory(factory)
    started_cpu = time.process_time_ns()
    started_wall = time.perf_counter_ns()
    try:
        if mode == "producer_ahead":
            for _ in range(count // 2):
                queue.put_nowait(payload)
                queue.put_nowait(payload)
                assert await read() == payload
                assert await read() == payload
        else:
            demand = asyncio.Event()
            consumed = asyncio.Event()

            async def produce() -> None:
                for _ in range(count):
                    if mode == "interleaved":
                        await demand.wait()
                        demand.clear()
                    await queue.put(payload)
                    if mode == "interleaved":
                        await consumed.wait()
                        consumed.clear()

            producer = asyncio.create_task(produce())
            try:
                for _ in range(count):
                    if mode == "interleaved":
                        assert queue.empty()
                        demand.set()
                    assert await read() == payload
                    consumed.set()
                await producer
            finally:
                if not producer.done():
                    producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
    finally:
        elapsed_wall = time.perf_counter_ns() - started_wall
        elapsed_cpu = time.process_time_ns() - started_cpu
        loop.set_task_factory(previous_factory)
    assert queue.empty()
    assert getattr(queue, "queued_bytes", 0) == 0
    return {
        "cpu_us_per_event": elapsed_cpu / count / 1000,
        "wall_us_per_event": elapsed_wall / count / 1000,
        "tasks_per_event": task_count / count,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--events", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    assert args.events > 0 and args.events % 2 == 0
    sys.path.insert(0, str(args.repo.resolve()))
    submit = importlib.import_module("app.modules.proxy._service.http_bridge.request_submit")
    streaming = importlib.import_module("app.modules.proxy._service.http_bridge.streaming")
    result: dict[str, Any] = {
        "head": subprocess.check_output(["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "-C", str(args.repo), "status", "--porcelain"], text=True)),
        "python": sys.version,
        "platform": platform.platform(),
        "events": args.events,
        "repeats": args.repeats,
        "bounded": hasattr(submit, "_HTTPBridgeLiveEventQueue"),
        "modes": {},
    }
    for mode in ("producer_ahead", "interleaved", "burst"):
        await measure(mode, 1000, submit, streaming, instrument=False)
        samples = [await measure(mode, args.events, submit, streaming, instrument=False) for _ in range(args.repeats)]
        counted = await measure(mode, args.events, submit, streaming, instrument=True)
        result["modes"][mode] = {
            "median_cpu_us_per_event": statistics.median(sample["cpu_us_per_event"] for sample in samples),
            "median_wall_us_per_event": statistics.median(sample["wall_us_per_event"] for sample in samples),
            "tasks_per_event": counted["tasks_per_event"],
            "samples": samples,
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
