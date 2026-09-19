#!/usr/bin/env python3
"""PreToolUse guard on the Agent tool: protect the Fable pool, record every dispatch.

Routing rule (router program, 2026-09-19): Fable is the driver and the judge;
Opus 5 is the default seat and is NOT rationed. So only two shapes are denied —
an explicit ``claude-fable-*`` model on a subagent, and a catch-all
``subagent_type`` that would silently inherit a Fable driver. Opus and every
other non-Fable model stay allowed. ``fork`` is always allowed (the context IS
the deliverable) but is written to the ledger with ``"fork": true``.

Every Agent call, allowed or denied, appends a C2 ``dispatch`` line to
``~/.claude/logs/dispatch.jsonl``. Fail-open: an I/O or parse error never blocks.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CATCH_ALL = {"general-purpose", "claude", ""}
FABLE = "fable"
CLASS_TAG = re.compile(r"^\s*\[class:([a-z0-9_-]+)\]", re.IGNORECASE)
LEDGER = Path(os.environ.get("DISPATCH_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl")
TABLE = Path(os.environ.get("ROUTING_TABLE") or Path.home() / ".claude" / "routing-table.json")

REASON = (
    "seat-guard: {what}. Fable is the driver and the judge — nothing else runs "
    "on it. Opus 5 is the default seat and is not rationed: pin "
    "model: 'claude-opus-5' (or dispatch the 'opus-seat' type), 'Explore' for "
    "read-only work, 'cursor-seat' for mechanical work, 'codex-verifier' for "
    "cross-vendor verification. Ask the router which seat to use: "
    "`route pick <class>`. Canon: ~/.agents/policy/coding-agents/ROUTING.md"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def task_class(prompt: str, subagent: str):
    """`[class:x]` tag wins; else the routing table's chains name the class."""
    match = CLASS_TAG.match(prompt or "")
    if match:
        return match.group(1).lower()
    if not subagent:
        return None
    try:
        classes = json.loads(TABLE.read_text()).get("classes") or {}
    except Exception:
        return None
    fallback = None
    for name, spec in classes.items():
        if not isinstance(spec, dict):
            continue
        seats = [str(entry.get("seat") or "") for entry in (spec.get("chain") or []) if isinstance(entry, dict)]
        if seats and seats[0].lower() == subagent:
            return name
        if fallback is None and subagent in [seat.lower() for seat in seats]:
            fallback = name
    return fallback


def append(record: dict) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        pass


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        return
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    subagent = str(tool_input.get("subagent_type") or "").strip().lower()
    model = str(tool_input.get("model") or "").strip().lower()

    record = {
        "ts": now(),
        "event": "dispatch",
        "session_id": payload.get("session_id"),
        "subagent_type": subagent or None,
        "model": model or None,
        "name": str(tool_input.get("name") or "") or None,
        "task_class": task_class(str(tool_input.get("prompt") or ""), subagent),
        "cwd": payload.get("cwd"),
    }

    if subagent == "fork":
        record["fork"] = True
        append(record)
        return

    denial = None
    if model and FABLE in model:
        denial = "this dispatch pins a Fable model on a subagent"
    elif not model and subagent in CATCH_ALL:
        denial = "this catch-all subagent_type would inherit the driver model"

    if denial:
        record["denied"] = True
        record["reason"] = denial
    append(record)
    if not denial:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": REASON.format(what=denial),
    }}))


if __name__ == "__main__":
    main()
