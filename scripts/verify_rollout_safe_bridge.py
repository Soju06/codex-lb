from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

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
    delete_owner_after_first_turn: bool
    postgresql_pod: str
    postgresql_user: str
    postgresql_database: str
    postgresql_secret: str
    kubectl_binary: str
    session_prefix: str


def _classify_response_failure(
    *,
    status_code: int,
    content_type: str | None,
    raw_body: str,
    parsed_body: dict[str, object] | None,
) -> str:
    normalized_content_type = (content_type or "").lower()
    normalized_body = raw_body.lower()
    if "text/html" in normalized_content_type or normalized_body.startswith("<html"):
        if status_code == 404:
            return "ingress_miss"
        return "ingress_unavailable"
    if parsed_body is not None:
        error = parsed_body.get("error")
        if isinstance(error, dict):
            error_payload = cast(dict[str, object], error)
            code = error_payload.get("code")
            if isinstance(code, str) and code:
                if code == "bridge_drain_active":
                    return "drain_rejected"
                if code in {"bridge_instance_mismatch", "previous_response_not_found"}:
                    return "bridge_continuity_failure"
                if code == "upstream_unavailable":
                    return "upstream_unavailable"
                return code
    if status_code >= 500:
        return "server_error"
    return "http_error"


def _request_stage(*, turn: int, owner_deleted: bool) -> str:
    if owner_deleted:
        return "reattach"
    if turn > 1:
        return "follow_up"
    return "first_turn"


async def _run_session(client: httpx.AsyncClient, config: VerifyConfig, session_index: int) -> dict[str, object]:
    session_id = f"{config.session_prefix}-{session_index}"
    turn_state: str | None = None
    previous_response_id: str | None = None
    events: list[dict[str, object]] = []
    owner_deleted = False

    for turn in range(1, config.turns + 1):
        stage = _request_stage(turn=turn, owner_deleted=owner_deleted)
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
            raw_body = response.text[:2000]
            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                return {
                    "session": session_index,
                    "ok": False,
                    "turn": turn,
                    "error": type(exc).__name__,
                    "detail": str(exc),
                    "status": response.status_code,
                    "raw_body": raw_body,
                    "content_type": response.headers.get("content-type"),
                    "classification": _classify_response_failure(
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type"),
                        raw_body=raw_body,
                        parsed_body=None,
                    ),
                    "request_stage": stage,
                    "headers": dict(headers),
                    "events": events,
                }
        except Exception as exc:
            classification = "upstream_timeout" if isinstance(exc, httpx.ReadTimeout) else type(exc).__name__
            if (
                owner_deleted
                and turn > 1
                and classification in {"upstream_timeout", "upstream_unavailable", "server_error"}
            ):
                classification = "owner_loss_recovery_failed"
            return {
                "session": session_index,
                "ok": False,
                "turn": turn,
                "error": type(exc).__name__,
                "detail": str(exc),
                "classification": classification,
                "request_stage": stage,
                "headers": dict(headers),
                "events": events,
            }
        events.append(
            {
                "turn": turn,
                "status": response.status_code,
                "content_type": response.headers.get("content-type"),
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
                "raw_body": raw_body,
                "content_type": response.headers.get("content-type"),
                "classification": _classify_response_failure(
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type"),
                    raw_body=raw_body,
                    parsed_body=body,
                ),
                "request_stage": stage,
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
                "raw_body": response.text[:2000],
                "content_type": response.headers.get("content-type"),
                "request_stage": stage,
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
                "raw_body": response.text[:2000],
                "content_type": response.headers.get("content-type"),
                "request_stage": stage,
                "events": events,
            }
        if config.delete_owner_after_first_turn and session_index == 0 and turn == 1 and not owner_deleted:
            try:
                owner_instance = await asyncio.to_thread(_lookup_owner_instance, config, session_id)
                if not owner_instance:
                    return {
                        "session": session_index,
                        "ok": False,
                        "turn": turn,
                        "error": "missing_owner_instance",
                        "events": events,
                    }
                await asyncio.to_thread(_delete_owner_pod, config, owner_instance)
                owner_deleted = True
                events.append(
                    {
                        "turn": turn,
                        "owner_deleted": owner_instance,
                        "request_stage": stage,
                    }
                )
            except Exception as exc:
                return {
                    "session": session_index,
                    "ok": False,
                    "turn": turn,
                    "error": "delete_owner_failed",
                    "detail": str(exc),
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
        config.kubectl_binary,
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
    mode = "steady"
    if config.delete_owner_after_first_turn:
        mode = "owner-loss"
    elif config.restart:
        mode = "overlap"
    return {
        "mode": mode,
        "passed": passed,
        "total": config.sessions,
        "failures": failures,
    }


def _parse_args() -> VerifyConfig:
    parser = argparse.ArgumentParser(
        description="Verify rollout-safe durable bridge continuity against a live cluster."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:18081/v1/responses")
    parser.add_argument("--host", default="codex-lb.localtest.me")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="gpt-5.1")
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--restart-delay-seconds", type=float, default=2.0)
    parser.add_argument("--rollout-namespace", default="codex-lb-e2e")
    parser.add_argument("--rollout-context", default="kind-codex-lb-local")
    parser.add_argument("--rollout-target", default="statefulset/codex-lb-e2e-workload")
    parser.add_argument("--delete-owner-after-first-turn", action="store_true")
    parser.add_argument("--postgresql-pod", default="codex-lb-e2e-postgresql-0")
    parser.add_argument("--postgresql-user", default="codexlb")
    parser.add_argument("--postgresql-database", default="codexlb")
    parser.add_argument("--postgresql-secret", default="codex-lb-e2e-postgresql")
    parser.add_argument("--kubectl-binary", default="kubectl")
    parser.add_argument("--session-prefix", default=f"verify-{uuid4().hex[:8]}")
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
        delete_owner_after_first_turn=args.delete_owner_after_first_turn,
        postgresql_pod=args.postgresql_pod,
        postgresql_user=args.postgresql_user,
        postgresql_database=args.postgresql_database,
        postgresql_secret=args.postgresql_secret,
        kubectl_binary=args.kubectl_binary,
        session_prefix=args.session_prefix,
    )


def _lookup_owner_instance(config: VerifyConfig, session_id: str) -> str | None:
    password = _read_postgresql_secret(config)
    sql = (
        "SELECT s.owner_instance_id "
        "FROM http_bridge_sessions s "
        "JOIN http_bridge_session_aliases a ON a.session_id = s.id "
        "WHERE a.alias_kind = 'session_header' "
        f"AND a.alias_value = '{_sql_literal(session_id)}' "
        "ORDER BY a.updated_at DESC "
        "LIMIT 1;"
    )
    cmd = [
        config.kubectl_binary,
        "--context",
        config.rollout_context,
        "-n",
        config.rollout_namespace,
        "exec",
        config.postgresql_pod,
        "--",
        "env",
        f"PGPASSWORD={password}",
        "psql",
        "-U",
        config.postgresql_user,
        "-d",
        config.postgresql_database,
        "-At",
        "-c",
        sql,
    ]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"owner_lookup_failed stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}")
    value = completed.stdout.strip()
    return value or None


def _delete_owner_pod(config: VerifyConfig, pod_name: str) -> None:
    cmd = [
        config.kubectl_binary,
        "--context",
        config.rollout_context,
        "-n",
        config.rollout_namespace,
        "delete",
        "pod",
        pod_name,
        "--wait=false",
    ]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"delete_owner_failed stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}")


def _read_postgresql_secret(config: VerifyConfig) -> str:
    cmd = [
        config.kubectl_binary,
        "--context",
        config.rollout_context,
        "-n",
        config.rollout_namespace,
        "get",
        "secret",
        config.postgresql_secret,
        "-o",
        "jsonpath={.data.password}",
    ]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"postgres_secret_failed stdout={completed.stdout.strip()} stderr={completed.stderr.strip()}"
        )
    secret = completed.stdout.strip()
    if not secret:
        raise RuntimeError("postgres_secret_missing")
    import base64

    return base64.b64decode(secret).decode("utf-8")


def _sql_literal(value: str) -> str:
    return value.replace("'", "''")


def main() -> int:
    config = _parse_args()
    result = asyncio.run(_run_verify(config))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["passed"] == result["total"] and not result["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
