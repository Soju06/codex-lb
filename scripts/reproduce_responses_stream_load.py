#!/usr/bin/env python3
"""Sustained /v1/responses streaming load reproducer.

This script intentionally keeps credentials out of the repository. Provide the
base URL and API key via environment variables when running it against a local
or staging codex-lb deployment:

  CODEX_LB_BASE_URL=https://example.test \
  CODEX_LB_API_KEY=... \
  uv run python scripts/reproduce_responses_stream_load.py --agents 6 --duration-seconds 3600
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Final

import aiohttp

DEFAULT_MODEL: Final[str] = "gpt-5.3-codex"


@dataclass(slots=True)
class WorkerStats:
    requests: int = 0
    completed: int = 0
    failed: int = 0
    heartbeats: int = 0
    first_byte_timeouts: int = 0
    status_429: int = 0
    other_status: int = 0


async def _run_worker(
    worker_id: int,
    *,
    base_url: str,
    api_key: str,
    model: str,
    deadline: float,
    first_byte_timeout_seconds: float,
) -> WorkerStats:
    stats = WorkerStats()
    url = f"{base_url.rstrip('/')}/v1/responses"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while time.monotonic() < deadline:
            stats.requests += 1
            payload = {
                "model": model,
                "stream": True,
                "prompt_cache_key": f"load-worker-{worker_id}",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Reply with one short sentence, then stop.",
                            }
                        ],
                    }
                ],
            }
            try:
                async with session.post(url, headers=headers, data=json.dumps(payload)) as response:
                    if response.status == 429:
                        stats.status_429 += 1
                    elif response.status >= 400:
                        stats.other_status += 1
                    first_byte_deadline = time.monotonic() + first_byte_timeout_seconds
                    saw_terminal = False
                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8", errors="replace")
                        if line.startswith(":") or "codex.keepalive" in line:
                            stats.heartbeats += 1
                        if time.monotonic() <= first_byte_deadline:
                            first_byte_deadline = 0
                        if "response.completed" in line or "response.failed" in line or "response.incomplete" in line:
                            saw_terminal = True
                            break
                    if first_byte_deadline and time.monotonic() > first_byte_deadline:
                        stats.first_byte_timeouts += 1
                    if saw_terminal and response.status < 400:
                        stats.completed += 1
                    else:
                        stats.failed += 1
            except (aiohttp.ClientError, asyncio.TimeoutError):
                stats.failed += 1
    return stats


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, default=6)
    parser.add_argument("--duration-seconds", type=float, default=3600.0)
    parser.add_argument("--first-byte-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    base_url = os.environ["CODEX_LB_BASE_URL"]
    api_key = os.environ["CODEX_LB_API_KEY"]
    deadline = time.monotonic() + args.duration_seconds
    results = await asyncio.gather(
        *(
            _run_worker(
                worker_id,
                base_url=base_url,
                api_key=api_key,
                model=args.model,
                deadline=deadline,
                first_byte_timeout_seconds=args.first_byte_timeout_seconds,
            )
            for worker_id in range(args.agents)
        )
    )
    total = WorkerStats()
    for result in results:
        total.requests += result.requests
        total.completed += result.completed
        total.failed += result.failed
        total.heartbeats += result.heartbeats
        total.first_byte_timeouts += result.first_byte_timeouts
        total.status_429 += result.status_429
        total.other_status += result.other_status
    print(json.dumps(total.__dict__, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
