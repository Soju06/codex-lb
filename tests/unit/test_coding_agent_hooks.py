"""The three coding-agent hooks, driven the way Claude Code drives them.

Each hook is a standalone stdlib script that reads one JSON payload on stdin,
so every test here runs the real file as a subprocess. Nothing is imported: an
import would hide the shebang, the fail-open behaviour and the exit status,
which is most of what these hooks promise.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "config" / "coding-agents" / "hooks"
SEAT_GUARD = HOOKS / "seat-guard.py"
CLOSEOUT = HOOKS / "subagent-closeout.py"
PULSE = HOOKS / "routing-pulse.py"
SESSION = "sess-router-0001"


def stamp(minutes_ago: float = 0.0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "dispatch.jsonl"


def run(script: Path, payload: dict, ledger: Path, home: Path, **env) -> subprocess.CompletedProcess:
    environment = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "DISPATCH_LEDGER": str(ledger),
        **env,
    }
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )


def lines(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]


def dispatch_payload(subagent: str, model: str = "", prompt: str = "do the thing", name: str = "") -> dict:
    tool_input: dict = {"subagent_type": subagent, "prompt": prompt}
    if model:
        tool_input["model"] = model
    if name:
        tool_input["name"] = name
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "session_id": SESSION,
        "cwd": "/repo",
        "tool_input": tool_input,
    }


def decision(result: subprocess.CompletedProcess):
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]


# --- seat-guard -------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    ["claude-fable-5-1", "claude-fable-5", "fable"],
)
def test_denies_a_fable_model_on_a_subagent(model, tmp_path, ledger):
    result = run(SEAT_GUARD, dispatch_payload("opus-seat", model), ledger, tmp_path)
    assert decision(result)["permissionDecision"] == "deny"
    assert "route pick" in decision(result)["permissionDecisionReason"]
    assert lines(ledger)[-1]["denied"] is True


@pytest.mark.parametrize("subagent", ["general-purpose", "claude", ""])
def test_denies_a_catch_all_with_no_model(subagent, tmp_path, ledger):
    result = run(SEAT_GUARD, dispatch_payload(subagent), ledger, tmp_path)
    assert decision(result)["permissionDecision"] == "deny"
    assert lines(ledger)[-1]["denied"] is True


@pytest.mark.parametrize(
    "subagent,model",
    [
        ("opus-seat", "claude-opus-5"),
        ("general-purpose", "claude-opus-5"),
        ("Explore", "claude-sonnet-5"),
        ("cursor-seat", "cursor-grok-4.6-medium-fast"),
        ("codex-verifier", "gpt-5.6-sol-xhigh"),
        ("verifier", ""),
    ],
)
def test_allows_every_non_fable_seat(subagent, model, tmp_path, ledger):
    result = run(SEAT_GUARD, dispatch_payload(subagent, model), ledger, tmp_path)
    assert result.stdout.strip() == ""
    record = lines(ledger)[-1]
    assert record["event"] == "dispatch"
    assert "denied" not in record


def test_fork_is_allowed_and_flagged(tmp_path, ledger):
    result = run(SEAT_GUARD, dispatch_payload("fork"), ledger, tmp_path)
    assert result.stdout.strip() == ""
    assert lines(ledger)[-1]["fork"] is True


def test_records_the_dispatch_fields(tmp_path, ledger):
    payload = dispatch_payload("opus-seat", "claude-opus-5", "[class:implement] ship it", name="lane-s2")
    run(SEAT_GUARD, payload, ledger, tmp_path)
    record = lines(ledger)[-1]
    assert record["session_id"] == SESSION
    assert record["subagent_type"] == "opus-seat"
    assert record["model"] == "claude-opus-5"
    assert record["name"] == "lane-s2"
    assert record["task_class"] == "implement"
    assert record["cwd"] == "/repo"
    assert record["ts"].endswith("Z")


def test_task_class_falls_back_to_the_routing_table(tmp_path, ledger):
    table = tmp_path / "routing-table.json"
    table.write_text(json.dumps({"classes": {
        "explore": {"chain": [{"seat": "Explore"}, {"seat": "cursor-seat"}]},
        "mechanical": {"chain": [{"seat": "cursor-seat"}]},
    }}))
    run(SEAT_GUARD, dispatch_payload("Explore", "claude-sonnet-5"), ledger, tmp_path, ROUTING_TABLE=str(table))
    assert lines(ledger)[-1]["task_class"] == "explore"
    # A seat that heads its own chain claims that class over one it only appears in.
    run(SEAT_GUARD, dispatch_payload("cursor-seat", "cursor-grok-4.6-medium-fast"), ledger, tmp_path,
        ROUTING_TABLE=str(table))
    assert lines(ledger)[-1]["task_class"] == "mechanical"


def test_task_class_finds_the_installed_table_without_an_override(tmp_path, ledger):
    """The hook and the `route` CLI must read the same installed table."""
    table = tmp_path / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    table.parent.mkdir(parents=True)
    table.write_text(json.dumps({"classes": {"verify": {"chain": [{"seat": "codex-verifier"}]}}}))
    run(SEAT_GUARD, dispatch_payload("codex-verifier", "gpt-5.6-sol-xhigh"), ledger, tmp_path)
    assert lines(ledger)[-1]["task_class"] == "verify"


def test_task_class_is_null_without_a_tag_or_table(tmp_path, ledger):
    run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-opus-5"), ledger, tmp_path,
        ROUTING_TABLE=str(tmp_path / "absent.json"))
    assert lines(ledger)[-1]["task_class"] is None


def test_ignores_tools_other_than_agent(tmp_path, ledger):
    payload = dispatch_payload("general-purpose")
    payload["tool_name"] = "Bash"
    result = run(SEAT_GUARD, payload, ledger, tmp_path)
    assert result.stdout.strip() == ""
    assert lines(ledger) == []


def test_fails_open_when_the_ledger_cannot_be_written(tmp_path):
    unwritable = tmp_path / "file" / "logs" / "dispatch.jsonl"
    unwritable.parent.parent.write_text("not a directory")
    allowed = run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-opus-5"), unwritable, tmp_path)
    assert allowed.returncode == 0 and allowed.stdout.strip() == ""
    denied = run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-fable-5-1"), unwritable, tmp_path)
    assert decision(denied)["permissionDecision"] == "deny"


def test_survives_garbage_on_stdin(tmp_path, ledger):
    result = subprocess.run(
        [sys.executable, str(SEAT_GUARD)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "DISPATCH_LEDGER": str(ledger)},
    )
    assert result.returncode == 0 and result.stdout.strip() == ""


# --- subagent-closeout ------------------------------------------------------


def stop_payload(agent_type: str, message: str = "Done, tests pass.", **extra) -> dict:
    return {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION,
        "agent_id": "agent-abc",
        "agent_type": agent_type,
        "last_assistant_message": message,
        **extra,
    }


def test_closeout_matches_the_newest_open_dispatch(tmp_path, ledger):
    run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-opus-5", "[class:explore] look", name="first"),
        ledger, tmp_path)
    run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-opus-5", "[class:implement] build", name="second"),
        ledger, tmp_path)
    run(CLOSEOUT, stop_payload("opus-seat"), ledger, tmp_path)
    record = lines(ledger)[-1]
    assert record["event"] == "closeout"
    assert record["name"] == "second"
    assert record["task_class"] == "implement"
    assert record["model"] == "claude-opus-5"
    assert record["matched"] is True
    assert record["ok"] is True
    assert record["duration_s"] >= 0

    # The second closeout falls through to the dispatch the first one left open.
    run(CLOSEOUT, stop_payload("opus-seat"), ledger, tmp_path)
    assert lines(ledger)[-1]["name"] == "first"


def test_closeout_prefers_a_name_the_payload_carries(tmp_path, ledger):
    run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-opus-5", "[class:explore] look", name="scout"),
        ledger, tmp_path)
    run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-opus-5", "[class:implement] build", name="builder"),
        ledger, tmp_path)
    run(CLOSEOUT, stop_payload("opus-seat", agent_name="scout"), ledger, tmp_path)
    assert lines(ledger)[-1]["task_class"] == "explore"


def test_closeout_never_matches_a_denied_dispatch(tmp_path, ledger):
    run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-fable-5-1", name="blocked"), ledger, tmp_path)
    run(CLOSEOUT, stop_payload("opus-seat"), ledger, tmp_path)
    record = lines(ledger)[-1]
    assert record["matched"] is False
    assert record["name"] is None


def test_closeout_ignores_another_session(tmp_path, ledger):
    run(SEAT_GUARD, dispatch_payload("opus-seat", "claude-opus-5", name="mine"), ledger, tmp_path)
    other = stop_payload("opus-seat")
    other["session_id"] = "sess-somebody-else"
    run(CLOSEOUT, other, ledger, tmp_path)
    assert lines(ledger)[-1]["matched"] is False


@pytest.mark.parametrize(
    "message,ok",
    [
        ("VERDICT pass. eval tail: 42 passed.", True),
        ("VERDICT fail — the eval never ran.", False),
        ("Reporting FABRICATION: the diff is empty.", False),
        ("CROSS-VENDOR-VIOLATION: author vendor is anthropic.", False),
        ("I cannot reach the worktree.", False),
        ("", False),
    ],
)
def test_closeout_reads_the_outcome_off_the_last_message(message, ok, tmp_path, ledger):
    run(SEAT_GUARD, dispatch_payload("verifier", "claude-opus-5"), ledger, tmp_path)
    run(CLOSEOUT, stop_payload("verifier", message), ledger, tmp_path)
    record = lines(ledger)[-1]
    assert record["ok"] is ok
    assert (record["error"] is None) is ok


def test_closeout_fails_open_without_a_ledger(tmp_path):
    unwritable = tmp_path / "file" / "dispatch.jsonl"
    unwritable.parent.write_text("not a directory")
    result = run(CLOSEOUT, stop_payload("opus-seat"), unwritable, tmp_path)
    assert result.returncode == 0


# --- routing-pulse ----------------------------------------------------------


class _Analytics(BaseHTTPRequestHandler):
    windows: dict = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        window = self.path.rsplit("=", 1)[-1]
        body = json.dumps({"seats": self.windows.get(window, [])}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def lb():
    """A stand-in for the agent-lb session map, keyed by window minutes."""
    handler = type("Handler", (_Analytics,), {"windows": {}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield handler, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def seat(model: str, requests: int, cost: float = 1.0) -> dict:
    return {"model": model, "requests": requests, "costUsd": cost}


def pulse(lb, ledger: Path, home: Path, session: str = SESSION):
    handler, url = lb
    return run(PULSE, {"hook_event_name": "UserPromptSubmit", "session_id": session},
               ledger, home, AGENT_LB_URL=url)


def context(result: subprocess.CompletedProcess):
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_pulse_fires_on_a_fable_burst(lb, tmp_path, ledger):
    handler, _ = lb
    handler.windows = {"60": [seat("claude-fable-5-1", 40)], "360": [seat("claude-fable-5-1", 40)]}
    fired = context(pulse(lb, ledger, tmp_path))
    assert "40 Fable requests in the last hour" in fired
    assert "route pick" in fired
    assert "ROUTING.md" in fired


def test_pulse_is_silent_below_the_burst_floor(lb, tmp_path, ledger):
    handler, _ = lb
    handler.windows = {"60": [seat("claude-fable-5-1", 39)], "360": [seat("claude-fable-5-1", 39)]}
    # Enough closeouts that the ratio trigger stays quiet, isolating the burst floor.
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("".join(
        json.dumps({"ts": stamp(5), "event": "closeout", "session_id": SESSION}) + "\n" for _ in range(10)
    ))
    assert context(pulse(lb, ledger, tmp_path)) is None


def test_pulse_ignores_opus_volume(lb, tmp_path, ledger):
    handler, _ = lb
    opus = [seat("claude-opus-5", 500), seat("claude-sonnet-5", 200)]
    handler.windows = {"60": opus, "360": opus}
    assert context(pulse(lb, ledger, tmp_path)) is None


def test_pulse_fires_when_closeouts_lag_the_six_hour_window(lb, tmp_path, ledger):
    handler, _ = lb
    handler.windows = {"60": [seat("claude-fable-5-1", 10)], "360": [seat("claude-fable-5-1", 40)]}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("".join(
        json.dumps({"ts": stamp(30), "event": "closeout", "session_id": SESSION}) + "\n" for _ in range(9)
    ))
    fired = context(pulse(lb, ledger, tmp_path))
    assert "40 Fable requests in 6h against 9 seat closeouts" in fired


def test_pulse_is_silent_when_closeouts_keep_up(lb, tmp_path, ledger):
    handler, _ = lb
    handler.windows = {"60": [seat("claude-fable-5-1", 10)], "360": [seat("claude-fable-5-1", 40)]}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("".join(
        json.dumps({"ts": stamp(30), "event": "closeout", "session_id": SESSION}) + "\n" for _ in range(10)
    ))
    assert context(pulse(lb, ledger, tmp_path)) is None


def test_pulse_ignores_closeouts_outside_the_window_and_other_sessions(lb, tmp_path, ledger):
    handler, _ = lb
    handler.windows = {"60": [seat("claude-fable-5-1", 10)], "360": [seat("claude-fable-5-1", 40)]}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    stale = [json.dumps({"ts": stamp(400), "event": "closeout", "session_id": SESSION}) for _ in range(20)]
    foreign = [json.dumps({"ts": stamp(5), "event": "closeout", "session_id": "other"}) for _ in range(20)]
    ledger.write_text("\n".join(stale + foreign) + "\n")
    assert "against 0 seat closeouts" in context(pulse(lb, ledger, tmp_path))


def test_pulse_throttles_the_second_prompt(lb, tmp_path, ledger):
    handler, _ = lb
    handler.windows = {"60": [seat("claude-fable-5-1", 60)], "360": [seat("claude-fable-5-1", 60)]}
    assert context(pulse(lb, ledger, tmp_path)) is not None
    assert context(pulse(lb, ledger, tmp_path)) is None
    # A different session keeps its own throttle.
    assert context(pulse(lb, ledger, tmp_path, session="sess-router-0002")) is not None


def test_pulse_is_silent_when_the_lb_is_down(tmp_path, ledger):
    result = run(PULSE, {"hook_event_name": "UserPromptSubmit", "session_id": SESSION},
                 ledger, tmp_path, AGENT_LB_URL="http://127.0.0.1:1")
    assert result.returncode == 0 and result.stdout.strip() == ""
    # A failed probe must not burn the throttle: the next prompt still checks.
    assert not (tmp_path / ".cache" / "routing-pulse" / SESSION).exists()


def test_pulse_ignores_a_short_session_id(tmp_path, ledger):
    result = run(PULSE, {"hook_event_name": "UserPromptSubmit", "session_id": "short"}, ledger, tmp_path,
                 AGENT_LB_URL="http://127.0.0.1:1")
    assert result.stdout.strip() == ""
