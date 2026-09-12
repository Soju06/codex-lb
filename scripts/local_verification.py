"""Verify local candidates and release packages without changing running services."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp

from scripts.local_verification_runtime import Report, VerificationError, child

MAX_FRAME = 1_048_576
ROUTE = "/backend-api/codex/responses"
DEFAULT_MODEL = "trae/GPT-5.6-Luna-max"


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise VerificationError("Expected a plain loopback HTTP base URL")
    if parsed.port is None:
        raise VerificationError("An explicit candidate port is required")
    return value.rstrip("/")


def event_json(raw: str) -> dict[str, Any]:
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeError):
        raise VerificationError("Invalid event JSON") from None
    if not isinstance(event, dict):
        raise VerificationError("Event must be an object")
    return event


def completion(event: dict[str, Any]) -> dict[str, Any] | None:
    kind = event.get("type")
    if kind in {"error", "response.failed", "response.incomplete", "response.cancelled"}:
        raise VerificationError("Upstream emitted a failure terminal")
    if kind != "response.completed":
        return None
    response = event.get("response")
    if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error"):
        raise VerificationError("Invalid completed response")
    if not isinstance(response.get("output"), list):
        raise VerificationError("Completion has no output array")
    return response


async def http_completion(client: aiohttp.ClientSession, url: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with client.post(url + ROUTE, json=payload, allow_redirects=False) as response:
        if response.status != 200:
            raise VerificationError(f"Inference HTTP status {response.status}")
        if response.content_type != "text/event-stream":
            raise VerificationError("Expected an SSE response")
        buffer = b""
        async for chunk in response.content.iter_chunked(16_384):
            buffer += chunk
            # CRLF can be split across transport chunks; decode only complete frames.
            buffer = buffer.replace(b"\r\n", b"\n")
            while b"\n\n" in buffer:
                frame, buffer = buffer.split(b"\n\n", 1)
                if len(frame) > MAX_FRAME:
                    raise VerificationError("SSE frame exceeds limit")
                try:
                    data = "\n".join(
                        line[5:].lstrip(" ") for line in frame.decode().split("\n") if line.startswith("data:")
                    )
                except UnicodeError:
                    raise VerificationError("Invalid SSE encoding") from None
                if not data:
                    continue
                if data == "[DONE]":
                    raise VerificationError("SSE ended without a completed response")
                result = completion(event_json(data))
                if result is not None:
                    return result
            if len(buffer) > MAX_FRAME:
                raise VerificationError("SSE frame exceeds limit")
        raise VerificationError("SSE ended without a completed response")


async def ws_completion(socket: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
    async for message in socket:
        if message.type != aiohttp.WSMsgType.TEXT:
            raise VerificationError("WebSocket closed before completion")
        if len(message.data.encode()) > MAX_FRAME:
            raise VerificationError("WebSocket frame exceeds limit")
        result = completion(event_json(message.data))
        if result is not None:
            return result
    raise VerificationError("WebSocket ended without completion")


def exact_text(response: dict[str, Any], marker: str) -> dict[str, Any]:
    try:
        text = "".join(p.get("text", "") for i in response["output"] for p in i.get("content", []))
    except (AttributeError, TypeError):
        raise VerificationError("Malformed response output") from None
    if text.strip() != marker:
        raise VerificationError("Marker was not recovered exactly")
    return {"exactMarkerRecovered": True}


def compact_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    output = response["output"]
    if len(output) != 1 or not isinstance(output[0], dict) or output[0].get("type") != "compaction":
        raise VerificationError("Expected one compaction item")
    encrypted = output[0].get("encrypted_content")
    if not isinstance(encrypted, str) or not encrypted.startswith("trae-compact-v1:"):
        raise VerificationError("Missing replayable TRAE compaction state")
    return output


async def bounded_object(response: aiohttp.ClientResponse) -> dict[str, Any]:
    raw = bytearray()
    async for chunk in response.content.iter_chunked(16_384):
        raw.extend(chunk)
        if len(raw) > MAX_FRAME:
            raise VerificationError("JSON response exceeds limit")
    try:
        return event_json(raw.decode())
    except UnicodeError:
        raise VerificationError("Invalid JSON encoding") from None


async def ready(client: aiohttp.ClientSession, url: str) -> None:
    while True:
        try:
            async with client.get(url + "/health/ready", allow_redirects=False) as response:
                if response.status == 200:
                    state = await bounded_object(response)
                    if state.get("status") != "ok":
                        raise VerificationError("Invalid readiness response")
                    return
                if response.status != 503:
                    raise VerificationError(f"Readiness HTTP status {response.status}")
        except (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError):
            pass
        await asyncio.sleep(0.25)


async def probe(
    report: Report,
    url: str,
    model: str,
    *,
    compaction: bool = False,
    replay_url: str | None = None,
    plan: Path | None = None,
) -> None:
    url, replay_url = local_url(url), local_url(replay_url or url)
    if replay_url != url and not compaction:
        raise VerificationError("Replay URL requires compaction verification")
    if plan:

        async def database() -> dict[str, Any]:
            import tempfile

            code = """import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts.local_database_gate import check_plan
for port in sys.argv[3:]: check_plan(Path(sys.argv[2]),int(port))
"""
            ports = [cast(int, urlsplit(candidate).port) for candidate in dict.fromkeys([url, replay_url])]
            with tempfile.TemporaryDirectory(prefix="codex-lb-db-check-") as directory:
                result = await child(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        code,
                        str(Path(__file__).resolve().parents[1]),
                        str(plan.resolve()),
                        *map(str, ports),
                    ],
                    cwd=Path(directory),
                    env=dict(os.environ),
                    log=Path(directory) / "check.log",
                )
            if result:
                raise VerificationError("Database compatibility check failed")
            return {"checkedPorts": ports}

        await report.run("database", database, lambda result: result)
    timeout = aiohttp.ClientTimeout(total=report.timeout, connect=min(5, report.timeout))
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
        await report.run("ready", lambda: ready(client, url), initial={"candidate": url, "model": model})
        if replay_url != url:
            await report.run("replay_ready", lambda: ready(client, replay_url), initial={"candidate": replay_url})
        marker = "VERIFY_" + uuid4().hex[:16]
        payload = {
            "model": model,
            "stream": True,
            "tools": [],
            "input": [
                {"type": "function_call_output", "name": "verification_context", "output": "retained tool result"},
                {"role": "user", "content": f"Reply exactly {marker}. Do not call tools."},
            ],
        }
        await report.run("http", lambda: http_completion(client, url, payload), lambda r: exact_text(r, marker))

        async def websocket_smoke() -> dict[str, Any]:
            async with client.ws_connect(url + ROUTE, max_msg_size=MAX_FRAME) as socket:
                await socket.send_json({"type": "response.create", **payload})
                return await ws_completion(socket)

        await report.run("websocket", websocket_smoke, lambda r: exact_text(r, marker))
        if not compaction:
            return
        # The marker appears only in initial history, never in subsequent requests.
        payload["reasoning"] = {"effort": "low"}
        payload["input"] = [
            {
                "role": "user",
                "content": f"Project marker: {marker}. Retain it. The report still needs P95 verification.",
            },
            {"type": "function_call_output", "name": "read_checkpoint", "output": "Initial scan completed."},
            {"type": "compaction_trigger"},
        ]
        first = await report.run(
            "http_compaction",
            lambda: http_completion(client, url, payload),
            lambda r: {"compactionItems": len(compact_items(r))},
        )

        # Each stage owns its socket, including setup/cleanup inside its deadline.
        async def ws_request(inputs: list[dict[str, Any]]) -> dict[str, Any]:
            async with client.ws_connect(replay_url + ROUTE, max_msg_size=MAX_FRAME) as socket:
                await socket.send_json({**payload, "type": "response.create", "input": inputs})
                return await ws_completion(socket)

        second = await report.run(
            "websocket_recompaction",
            lambda: ws_request(compact_items(first) + [{"type": "compaction_trigger"}]),
            lambda r: {"compactionItems": len(compact_items(r)), "crossReplica": replay_url != url},
        )
        await report.run(
            "summary_replay",
            lambda: ws_request(
                compact_items(second)
                + [
                    {"role": "user", "content": "What exact project marker is retained? Reply only with that marker."},
                ]
            ),
            lambda r: exact_text(r, marker),
        )


def idle_reason(checks: dict[str, Any], entry: dict[str, Any], port: int) -> str | None:
    if (
        entry.get("ok") is not True
        or type(entry.get("port")) is not int
        or not isinstance(entry.get("connections"), dict)
    ):
        return "Invalid entrance telemetry"
    if entry["port"] == port:
        return "Candidate is the active entrance"
    connections = entry["connections"].get(str(port))
    if type(connections) is not int or connections < 0:
        return "Missing entrance connection count"
    if checks.get("in_flight") != "0":
        return "Active requests or missing in-flight telemetry"
    if checks.get("draining") not in {"true", "false"} or "http_bridge_activity_error" in checks:
        return "Invalid drain or bridge telemetry"
    cold = set(checks) == {"draining", "bridge_drain_active", "in_flight"} and checks["bridge_drain_active"] == "false"
    if checks.get("http_bridge_restart_blocking") != "false" and not (cold and connections == 0):
        return "Bridge active or missing bridge telemetry"
    if connections and checks["draining"] != "true":
        return "Connected candidate has not entered drain"
    return None


async def idle(report: Report, url: str, control: Path) -> None:
    url = local_url(url)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:

        async def sample() -> None:
            reader, writer = await asyncio.open_unix_connection(control, limit=MAX_FRAME)
            try:
                writer.write(b'{"action":"status"}\n')
                await writer.drain()
                entry = event_json((await reader.readline()).decode())
            finally:
                writer.close()
                await writer.wait_closed()
            async with client.get(url + "/internal/drain/status", allow_redirects=False) as response:
                if response.status != 200:
                    raise VerificationError("Drain status unavailable")
                state = await bounded_object(response)
            if state.get("status") != "ok" or not isinstance(state.get("checks"), dict):
                raise VerificationError("Invalid drain status")
            reason = idle_reason(state["checks"], entry, cast(int, urlsplit(url).port))
            if reason:
                raise VerificationError(reason)

        await report.run("idle_first_sample", sample)
        await asyncio.sleep(2)
        await report.run("idle_second_sample", sample)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--report", type=Path, required=True, help="New report file; existing evidence is never overwritten"
    )
    root.add_argument("--timeout", type=float, default=120, help="Deadline per stage in seconds")
    commands = root.add_subparsers(dest="command", required=True)
    probes = commands.add_parser("probe")
    probes.add_argument("--url", required=True)
    probes.add_argument("--model", default=DEFAULT_MODEL)
    probes.add_argument("--compaction", action="store_true")
    probes.add_argument("--replay-url")
    probes.add_argument("--plan", type=Path)
    check = commands.add_parser("idle")
    check.add_argument("--url", required=True)
    check.add_argument("--control", type=Path, required=True)
    tests = commands.add_parser("test-release")
    tests.add_argument("--release", type=Path, required=True)
    tests.add_argument("--tests-repo", type=Path, default=Path(__file__).resolve().parents[1])
    tests.add_argument("--fixture-ref", required=True)
    tests.add_argument("targets", nargs="+")
    return root


async def main() -> None:
    args = parser().parse_args()
    report = Report(args.report, args.timeout)
    try:
        if args.command == "probe":
            await probe(
                report, args.url, args.model, compaction=args.compaction, replay_url=args.replay_url, plan=args.plan
            )
        elif args.command == "idle":
            await idle(report, args.url, args.control)
        else:
            from scripts.local_release_tests import test_release

            await test_release(report, args.release, args.tests_repo, args.fixture_ref, args.targets)
    except BaseException as exc:
        report.finish("cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else "failed")
        if not isinstance(exc, Exception):
            raise
        print(
            json.dumps(
                {
                    "verification": "failed",
                    "error": str(exc) if isinstance(exc, VerificationError) else type(exc).__name__,
                }
            ),
            flush=True,
        )
        raise SystemExit(1) from None
    else:
        report.finish("passed")


if __name__ == "__main__":
    asyncio.run(main())
