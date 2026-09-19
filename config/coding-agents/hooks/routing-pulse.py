#!/usr/bin/env python3
"""UserPromptSubmit hook: nudge a session that is spending Fable on volume work.

Fable is the binding pool and the only rationed one (router program,
2026-09-19), so the driver models here are Fable and nothing else — Opus is a
seat, not a cost to warn about. Two triggers: a burst (Fable requests in the
last hour at or above the burst floor) or a ratio (enough Fable requests over
six hours with too few seat closeouts recorded in the C2 ledger to match).

Reads the session's own numbers from the agent-lb session map. Throttled per
session, silent on any error or when the LB is down.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

LB = os.environ.get("AGENT_LB_URL") or "http://127.0.0.1:2455"
CANON = "~/.agents/policy/coding-agents/ROUTING.md"
LEDGER = Path(os.environ.get("DISPATCH_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl")
DRIVER_MODELS = ("fable",)
BURST_MINUTES = 60
BURST_MIN_REQUESTS = 40
RATIO_MINUTES = 360
RATIO_MIN_REQUESTS = 25
CLOSEOUT_RATIO_FLOOR = 0.25
THROTTLE_SECONDS = 900


def fable_requests(session_id: str, window_minutes: int):
    url = f"{LB}/api/sessions/{session_id}/analytics?windowMinutes={window_minutes}"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.load(response)
    except Exception:
        return None, 0.0
    requests = cost = 0.0
    for seat in data.get("seats") or []:
        model = str(seat.get("model") or "")
        if any(driver in model for driver in DRIVER_MODELS):
            requests += float(seat.get("requests") or 0)
            cost += float(seat.get("costUsd") or 0)
    return requests, cost


def closeouts(session_id: str, window_minutes: int) -> int:
    floor = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    count = 0
    try:
        with LEDGER.open() as handle:
            lines = handle.readlines()[-4000:]
    except Exception:
        return 0
    for line in lines:
        try:
            record = json.loads(line)
            if record.get("event") != "closeout" or record.get("session_id") != session_id:
                continue
            if datetime.fromisoformat(str(record.get("ts")).replace("Z", "+00:00")) >= floor:
                count += 1
        except Exception:
            continue
    return count


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    event = payload.get("hook_event_name") or "UserPromptSubmit"
    session_id = os.environ.get("ROUTING_PULSE_SESSION_ID") or payload.get("session_id")
    if not isinstance(session_id, str) or len(session_id) < 8:
        return

    # Throttle on a COMPLETED check, not only on a firing one: a quiet session
    # would otherwise re-hit the LB over HTTP on every prompt.
    throttle = Path.home() / ".cache" / "routing-pulse" / session_id
    try:
        if throttle.exists() and time.time() - throttle.stat().st_mtime < THROTTLE_SECONDS:
            return
    except OSError:
        return

    burst, _ = fable_requests(session_id, BURST_MINUTES)
    if burst is None:
        return
    window, cost = fable_requests(session_id, RATIO_MINUTES)
    try:
        throttle.parent.mkdir(parents=True, exist_ok=True)
        throttle.touch()
    except OSError:
        pass

    closed = closeouts(session_id, RATIO_MINUTES)
    is_burst = burst >= BURST_MIN_REQUESTS
    is_ratio = (window or 0) >= RATIO_MIN_REQUESTS and closed < CLOSEOUT_RATIO_FLOOR * (window or 0)
    if not (is_burst or is_ratio):
        return

    trigger = (
        f"{int(burst)} Fable requests in the last hour"
        if is_burst
        else f"{int(window or 0)} Fable requests in {RATIO_MINUTES // 60}h against {closed} seat closeouts"
    )
    context = (
        f"ROUTING PULSE (agent-lb session map): {trigger} (${cost:.2f}). Fable is "
        f"the binding pool and this session is the driver, so volume work here "
        f"costs the one thing that runs out. Opus 5 is the default seat and is "
        f"NOT rationed: dispatch it freely. Ask the router which seat a piece of "
        f"work belongs to — `route pick <class>` (explore | implement | "
        f"mechanical | verify | review | computer) — and dispatch that seat "
        f"instead of doing the work yourself. Canon: {CANON}. Live numbers: "
        f"{LB}/s/{session_id[:8]}"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": context,
    }}))


if __name__ == "__main__":
    main()
