from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import cast

import httpx


@dataclass(frozen=True, slots=True)
class VerifyConfig:
    base_url: str
    host: str
    api_key: str
    model: str
    sessions: int
    turns: int
    restart: bool
    restart_delay_seconds: float
    rollout_namespace: str
    rollout_context: str
    rollout_target: str


async def _run_session(client: httpx.AsyncClient, config: VerifyConfig, session_index: int) -> dict[str, object]:
    session_id = f"verify-{session_index}"
    turn_state: str | None = None
    previous_response_id: str | None = None
    events: list[dict[str, object]] = []

    for turn in range(1, config.turns + 1):
        headers = {
            "Host": config.host,
            "Authorization": f"Bearer {config.api_key}",
            "x-codex-session-id": session_id,
        }
        if turn_state is not None:
            headers["x-codex-turn-state"] = turn_state
        payload: dict[str, object] = {
            "model": config.model,
            "instructions": "Reply with OK only.",
            "input": f"session {session_index} turn {turn}",
        }
        if previous_response_id is not None:
            payload["previous_response_id"] = previous_response_id
        started = time.monotonic()
        try:
            response = await client.post(
                config.base_url,
                headers=headers,
                json=payload,
                timeout=180.0,
            )
            body = response.json()
        except Exception as exc:
            return {
                "session": session_index,
                "ok": False,
                "turn": turn,
                "error": type(exc).__name__,
                "detail": str(exc),
                "events": events,
            }
        events.append(
            {
                "turn": turn,
                "status": response.status_code,
                "turn_state": response.headers.get("x-codex-turn-state"),
                "response_id": body.get("id") if isinstance(body, dict) else None,
                "latency_seconds": round(time.monotonic() - started, 2),
            }
        )
        if response.status_code != 200:
            return {
                "session": session_index,
                "ok": False,
                "turn": turn,
                "status": response.status_code,
                "body": body,
                "events": events,
            }
        response_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(response_id, str) or not response_id:
            return {
                "session": session_index,
                "ok": False,
                "turn": turn,
                "error": "missing_response_id",
                "body": body,
                "events": events,
            }
        previous_response_id = response_id
        turn_state = response.headers.get("x-codex-turn-state", turn_state)
        if turn_state is None:
            return {
                "session": session_index,
                "ok": False,
                "turn": turn,
                "error": "missing_turn_state",
                "body": body,
                "events": events,
            }

    return {
        "session": session_index,
        "ok": True,
        "events": events,
    }


async def _trigger_restart(config: VerifyConfig) -> None:
    await asyncio.sleep(config.restart_delay_seconds)
    cmd = [
        "kubectl",
        "--context",
        config.rollout_context,
        "-n",
        config.rollout_namespace,
        "rollout",
        "restart",
        config.rollout_target,
    ]
    completed = await asyncio.to_thread(
        subprocess.run,
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"restart_failed stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}",
        )


async def _run_verify(config: VerifyConfig) -> dict[str, object]:
    async with httpx.AsyncClient() as client:
        session_tasks = [asyncio.create_task(_run_session(client, config, idx)) for idx in range(config.sessions)]
        tasks: list[asyncio.Task[object]] = [*session_tasks]
        if config.restart:
            tasks.append(asyncio.create_task(_trigger_restart(config)))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    failures: list[object] = []
    session_results: list[dict[str, object]] = []
    for result in results:
        if isinstance(result, Exception):
            failures.append({"restart_error": type(result).__name__, "detail": str(result)})
            continue
        if isinstance(result, dict) and "session" in result:
            session_results.append(cast(dict[str, object], result))

    failures.extend(result for result in session_results if not bool(result["ok"]))
    passed = sum(1 for result in session_results if bool(result["ok"]))
    return {
        "mode": "overlap" if config.restart else "steady",
        "passed": passed,
        "total": config.sessions,
        "failures": failures,
    }


def _parse_args() -> VerifyConfig:
    parser = argparse.ArgumentParser(
        description="Verify rollout-safe durable bridge continuity against a live cluster."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18081/v1/responses")
    parser.add_argument("--host", default="codex-lb-e2e.localtest.me")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="gpt-5.1")
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--restart-delay-seconds", type=float, default=2.0)
    parser.add_argument("--rollout-namespace", default="codex-lb-e2e")
    parser.add_argument("--rollout-context", default="kind-codex-lb-local")
    parser.add_argument("--rollout-target", default="statefulset/codex-lb-e2e-workload")
    args = parser.parse_args()
    return VerifyConfig(
        base_url=args.base_url,
        host=args.host,
        api_key=args.api_key,
        model=args.model,
        sessions=args.sessions,
        turns=args.turns,
        restart=args.restart,
        restart_delay_seconds=args.restart_delay_seconds,
        rollout_namespace=args.rollout_namespace,
        rollout_context=args.rollout_context,
        rollout_target=args.rollout_target,
    )


def main() -> int:
    config = _parse_args()
    result = asyncio.run(_run_verify(config))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["passed"] == result["total"] and not result["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
