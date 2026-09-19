#!/usr/bin/env python3
"""SubagentStop hook: close the C2 dispatch ledger line a seat opened.

The payload carries ``session_id``, ``agent_id``, ``agent_type`` (the seat's
frontmatter name) and ``last_assistant_message`` — but not the ``name`` the
caller passed to the Agent tool, so the pair needs a join key. A subagent's own
transcript opens with the Agent prompt verbatim (verified 2026-09-19 against
``…/<session>/subagents/agent-*.jsonl``), so hashing that first user message
reproduces the ``prompt_sha256`` seat-guard wrote: an exact join, recorded as
``"match": "prompt_hash"``. When the transcript is missing or unparsable this
falls back to the newest open dispatch for the seat, recorded as
``"match": "fallback"``. ``ok`` is read off the last message, the only outcome
signal the payload has.

Appends one C2 ``closeout`` line. Fail-open: it never blocks a subagent.
"""

import hashlib
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


def prompt_hash(payload: dict):
    """sha256 of the prompt this subagent was launched with, from its transcript."""
    path = str(payload.get("agent_transcript_path") or "")
    if not path:
        # Only the subagent's own transcript will do; the session transcript
        # opens with the user's prompt, not the Agent tool's.
        candidate = str(payload.get("transcript_path") or "")
        path = candidate if "/subagents/" in candidate else ""
    if not path:
        return None
    try:
        with open(path) as handle:
            for line in handle:
                entry = json.loads(line)
                message = entry.get("message") or {}
                if message.get("role") != "user":
                    continue
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(
                        block.get("text") or ""
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if not isinstance(content, str):
                    return None
                return hashlib.sha256(content.encode("utf-8")).hexdigest()
    except Exception:
        return None
    return None


def resolve(open_dispatches: list, digest: str, seat: str, name: str):
    """One dispatch out of those still open, newest first, and how it was found."""
    if digest:
        for record in reversed(open_dispatches):
            if record.get("prompt_sha256") == digest:
                return record, "prompt_hash"
    for key, wanted in (("name", name), ("subagent_type", seat)):
        if not wanted:
            continue
        for record in reversed(open_dispatches):
            if str(record.get(key) or "").lower() == wanted.lower():
                return record, "fallback"
    return None, "fallback"


def match(records: list, session_id, agent_type: str, agent_name: str, digest):
    """The dispatch this stop belongs to, and how it was found.

    Dispatches go on one open list; replaying each past closeout through the
    same `resolve` removes exactly the dispatch that closeout was attributed to.
    Separate per-key stacks used to diverge here: a closeout matched by prompt
    hash still popped the newest dispatch off its seat stack, so a sibling seat
    was closed twice and its real dispatch never at all.
    """
    open_dispatches: list = []
    for record in records:
        if record.get("session_id") != session_id:
            continue
        if record.get("event") == "dispatch" and not record.get("denied"):
            open_dispatches.append(record)
        elif record.get("event") == "closeout":
            closed, _ = resolve(
                open_dispatches,
                str(record.get("prompt_sha256") or ""),
                str(record.get("subagent_type") or record.get("agent_type") or ""),
                str(record.get("name") or ""),
            )
            if closed is not None:
                open_dispatches.remove(closed)
    return resolve(open_dispatches, digest or "", agent_type, agent_name)


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

    digest = prompt_hash(payload)
    dispatch, how = match(tail(), session_id, agent_type, agent_name, digest)
    ok = bool(last.strip()) and not FAILURE.search(last)
    seat = agent_type.lower() or (dispatch or {}).get("subagent_type")
    record = {
        "ts": stamp(now()),
        "event": "closeout",
        "session_id": session_id,
        # agent_type is what the payload calls it; subagent_type is the C2 name
        # S3's `route report` joins on. Both carry the same value.
        "agent_type": agent_type or (dispatch or {}).get("subagent_type"),
        "subagent_type": seat,
        "name": agent_name or (dispatch or {}).get("name"),
        "agent_id": payload.get("agent_id"),
        "task_class": (dispatch or {}).get("task_class"),
        "model": (dispatch or {}).get("model"),
        # The hash of the dispatch this closeout was ATTRIBUTED to, so replaying
        # the ledger resolves it back to the same row. A digest that matched
        # nothing is kept separately rather than written here as if it had.
        "prompt_sha256": digest if how == "prompt_hash" else (dispatch or {}).get("prompt_sha256"),
        "digest_seen": digest if (digest and how != "prompt_hash") else None,
        "duration_s": duration(dispatch),
        "ok": ok,
        "error": None if ok else (last.strip()[:300] or "no final message"),
        "matched": bool(dispatch),
        "match": how if dispatch else None,
    }
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        return


if __name__ == "__main__":
    main()
