#!/usr/bin/env python3
"""SubagentStop hook: close the C2 dispatch ledger line a seat opened.

The SubagentStop payload carries ``session_id``, ``agent_id``, ``agent_type``
(the seat's frontmatter name) and ``last_assistant_message`` — but not the
``name`` the caller passed to the Agent tool. So: match on a name field when
the payload happens to carry one, otherwise on ``agent_type`` against the most
recent dispatch in this session that has no closeout yet. ``ok`` is read off
the subagent's last message, which is the only outcome signal the payload has.

Appends one C2 ``closeout`` line. Fail-open: it never blocks a subagent.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path(os.environ.get("DISPATCH_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl")
TAIL_LINES = 4000
FAILURE = re.compile(
    r"\b(fabrication|cross-vendor-violation|verdict:?\s*fail|blocked|i (?:cannot|can't|was unable)"
    r"|error:|failed to|traceback \(most recent)",
    re.IGNORECASE,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def tail() -> list:
    try:
        with LEDGER.open() as handle:
            lines = handle.readlines()[-TAIL_LINES:]
    except Exception:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except Exception:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def match(records: list, session_id, agent_type: str, agent_name: str):
    """Newest dispatch for this seat that no closeout has claimed yet.

    Every dispatch is pushed onto a stack keyed by its seat and, when it has
    one, by its caller-given name; a closeout pops the same stacks. What is
    left on top is the dispatch still in flight.
    """
    by_seat: dict = {}
    by_name: dict = {}
    for record in records:
        if record.get("session_id") != session_id:
            continue
        stacks = []
        seat = str(record.get("subagent_type") or "").lower()
        name = str(record.get("name") or "").lower()
        if seat:
            stacks.append(by_seat.setdefault(seat, []))
        if name:
            stacks.append(by_name.setdefault(name, []))
        for stack in stacks:
            if record.get("event") == "dispatch" and not record.get("denied"):
                stack.append(record)
            elif record.get("event") == "closeout" and stack:
                stack.pop()
    if agent_name and by_name.get(agent_name.lower()):
        return by_name[agent_name.lower()][-1]
    stack = by_seat.get(agent_type.lower()) or []
    return stack[-1] if stack else None


def duration(dispatch) -> float:
    if not dispatch:
        return None
    try:
        started = datetime.fromisoformat(str(dispatch.get("ts")).replace("Z", "+00:00"))
    except Exception:
        return None
    return round((now() - started).total_seconds(), 1)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    session_id = payload.get("session_id")
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    agent_name = str(payload.get("agent_name") or payload.get("name") or "").strip()
    last = str(payload.get("last_assistant_message") or "")

    dispatch = match(tail(), session_id, agent_type, agent_name)
    ok = bool(last.strip()) and not FAILURE.search(last)
    record = {
        "ts": stamp(now()),
        "event": "closeout",
        "session_id": session_id,
        "subagent_type": agent_type.lower() or (dispatch or {}).get("subagent_type"),
        "name": agent_name or (dispatch or {}).get("name"),
        "agent_id": payload.get("agent_id"),
        "task_class": (dispatch or {}).get("task_class"),
        "model": (dispatch or {}).get("model"),
        "duration_s": duration(dispatch),
        "ok": ok,
        "error": None if ok else (last.strip()[:300] or "no final message"),
        "matched": bool(dispatch),
    }
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        return


if __name__ == "__main__":
    main()
