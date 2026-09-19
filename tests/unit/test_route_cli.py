"""Contract C4 of the router program: the `route` CLI, driven as a subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "clients" / "route"
TABLE = REPO / "config" / "coding-agents" / "routing-table.json"

UNREACHABLE_LB = "http://127.0.0.1:1"


def run(
    *args: str,
    home: Path,
    table: Path | None = None,
    fixtures: Path | None = None,
    base_url: str = UNREACHABLE_LB,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("ROUTE_", "AGENT_LB_"))}
    env["HOME"] = str(home)
    env["AGENT_LB_URL"] = base_url
    env["ROUTE_TABLE"] = str(table or TABLE)
    if fixtures is not None:
        env["ROUTE_FIXTURE_DIR"] = str(fixtures)
    env.update(extra or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
        check=False,
    )


def write_fixture(directory: Path, name: str, payload: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def pools_fixture(directory: Path, statuses: dict[str, str]) -> None:
    write_fixture(
        directory,
        "api_pools.json",
        {
            "generatedAt": "2026-09-19T21:00:00Z",
            "pools": [
                {
                    "id": pool_id,
                    "provider": pool_id.split("-")[0],
                    "kind": "weekly",
                    "accounts": 2,
                    "eligibleAccounts": 2,
                    "headroomPercent": 0.0 if status == "exhausted" else 60.0,
                    "aggregateRemainingPercent": 0.0 if status == "exhausted" else 60.0,
                    "resetAt": "2026-09-23T11:00:00Z",
                    "status": status,
                    "source": "scoped_marker",
                }
                for pool_id, status in statuses.items()
            ],
        },
    )


def ledger(home: Path, *, closeouts: int, ok: bool) -> None:
    path = home / ".claude" / "logs" / "dispatch.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    lines = []
    for index in range(closeouts):
        stamp = now - timedelta(hours=index + 1)
        name = f"seat-run-{index}"
        lines.append(
            {
                "ts": stamp.isoformat().replace("+00:00", "Z"),
                "event": "dispatch",
                "session_id": "session-1",
                "subagent_type": "opus-seat",
                "model": "claude-opus-5",
                "name": name,
                "task_class": "implement",
                "cwd": str(home),
            }
        )
        lines.append(
            {
                "ts": (stamp + timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
                "event": "closeout",
                "session_id": "session-1",
                "subagent_type": "opus-seat",
                "name": name,
                "duration_s": 240,
                "ok": ok,
                "error": None if ok else "seat failed",
            }
        )
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    (path / ".claude").mkdir(parents=True)
    return path


def test_script_is_an_executable_stdlib_client() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")


def test_pick_excludes_the_author_vendor_on_a_cross_vendor_class(home: Path, tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    pools_fixture(fixtures, {"anthropic-general": "ok", "openai-codex": "ok", "cursor": "ok"})

    anthropic = run("pick", "verify", "--author-vendor", "anthropic", "--json", home=home, fixtures=fixtures)
    openai = run("pick", "verify", "--author-vendor", "openai", "--json", home=home, fixtures=fixtures)
    missing = run("pick", "verify", "--json", home=home, fixtures=fixtures)

    assert anthropic.returncode == 0, anthropic.stderr
    assert json.loads(anthropic.stdout)["seat"] == "codex-verifier"
    assert openai.returncode == 0, openai.stderr
    assert json.loads(openai.stdout)["seat"] == "verifier"
    assert missing.returncode == 2
    assert "--author-vendor" in missing.stderr


def test_pick_skips_a_seat_whose_pool_is_exhausted(home: Path, tmp_path: Path) -> None:
    healthy = tmp_path / "healthy"
    pools_fixture(healthy, {"anthropic-general": "ok", "openai-codex": "ok"})
    drained = tmp_path / "drained"
    pools_fixture(drained, {"anthropic-general": "exhausted", "openai-codex": "ok"})

    before = run("pick", "implement", "--json", home=home, fixtures=healthy)
    after = run("pick", "implement", "--json", home=home, fixtures=drained)

    assert json.loads(before.stdout)["seat"] == "opus-seat"
    assert after.returncode == 0, after.stderr
    picked = json.loads(after.stdout)
    assert picked["seat"] == "implementer"
    assert "anthropic-general exhausted" in picked["reason"]


def test_pick_skips_a_seat_recorded_down_in_routing_state(home: Path, tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    pools_fixture(fixtures, {"anthropic-general": "ok", "openai-codex": "ok"})
    (home / ".claude" / "routing-state.json").write_text(
        json.dumps({"ts": "2026-09-19T21:00:00Z", "seats": {"opus-seat": {"ok": False, "error": "HTTP 429"}}}),
        encoding="utf-8",
    )

    result = run("pick", "implement", "--json", home=home, fixtures=fixtures)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["seat"] == "implementer"


def test_pick_moves_an_overridden_entry_to_the_end_of_its_chain(home: Path, tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    pools_fixture(fixtures, {"anthropic-general": "ok", "openai-codex": "ok"})
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    table["overrides"] = [
        {
            "class": "implement",
            "demote": {"seat": "opus-seat", "model": "claude-opus-5"},
            "reason": "ok-rate 30% over 10 closeouts",
            "evidence": {"closeouts": 10},
            "ts": "2026-09-19T21:00:00Z",
        }
    ]
    demoted = tmp_path / "demoted-table.json"
    demoted.write_text(json.dumps(table), encoding="utf-8")

    result = run("pick", "implement", "--json", home=home, table=demoted, fixtures=fixtures)

    assert result.returncode == 0, result.stderr
    picked = json.loads(result.stdout)
    assert picked["seat"] == "implementer"
    assert [entry["seat"] for entry in picked["fallbacks"]] == ["cursor-seat", "opus-seat"]


def test_pick_exits_two_when_no_chain_entry_is_routable(home: Path, tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    pools_fixture(fixtures, {"anthropic-general": "exhausted", "openai-codex": "exhausted", "cursor": "exhausted"})

    result = run("pick", "implement", home=home, fixtures=fixtures)

    assert result.returncode == 2
    assert "nothing routable" in result.stderr


def test_pools_falls_back_to_the_accounts_endpoint_when_the_pools_endpoint_is_missing(
    home: Path, tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    write_fixture(
        fixtures,
        "api_accounts.json",
        {
            "accounts": [
                {
                    "provider": "anthropic",
                    "status": "active",
                    "fableEligible": True,
                    "usage": {"secondaryRemainingPercent": 80.0},
                    "resetAtSecondary": "2026-09-23T11:00:00Z",
                },
                {
                    "provider": "anthropic",
                    "status": "active",
                    "fableEligible": False,
                    "usage": {"secondaryRemainingPercent": 20.0},
                    "resetAtSecondary": "2026-09-21T11:00:00Z",
                },
                {
                    "provider": "anthropic",
                    "status": "quota_exceeded",
                    "fableEligible": False,
                    "usage": {"secondaryRemainingPercent": 0.0},
                    "resetAtSecondary": "2026-09-20T11:00:00Z",
                },
                {
                    "provider": "openai",
                    "status": "active",
                    "usage": {"secondaryRemainingPercent": 90.0},
                    "resetAtSecondary": "2026-09-26T11:00:00Z",
                },
            ]
        },
    )

    result = run("pools", "--json", home=home, fixtures=fixtures)

    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["source"] == "accounts_fallback"
    pools = {pool["id"]: pool for pool in document["pools"]}
    assert pools["anthropic-fable"]["kind"] == "fable_scoped"
    assert pools["anthropic-fable"]["headroomPercent"] == 80.0
    assert pools["anthropic-general"]["accounts"] == 3
    assert pools["anthropic-general"]["eligibleAccounts"] == 2
    assert pools["anthropic-general"]["headroomPercent"] == 80.0
    assert pools["anthropic-general"]["aggregateRemainingPercent"] == 50.0
    assert pools["anthropic-general"]["resetAt"] == "2026-09-21T11:00:00Z"
    assert pools["anthropic-general"]["status"] == "ok"
    assert pools["openai-codex"]["status"] == "ok"
    assert all(pool["source"] == "accounts_fallback" for pool in document["pools"])


def test_pools_passes_a_real_c1_document_through_including_a_null_headroom(home: Path, tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    write_fixture(
        fixtures,
        "api_pools.json",
        {
            "generatedAt": "2026-09-19T21:00:00Z",
            "pools": [
                {
                    "id": "anthropic-fable",
                    "provider": "anthropic",
                    "kind": "fable_scoped",
                    "accounts": 5,
                    "eligibleAccounts": 0,
                    "headroomPercent": None,
                    "aggregateRemainingPercent": None,
                    "resetAt": None,
                    "status": "exhausted",
                    "source": "scoped_marker",
                }
            ],
        },
    )

    pretty = run("pools", home=home, fixtures=fixtures)
    raw = run("pools", "--json", home=home, fixtures=fixtures)

    assert pretty.returncode == 0, pretty.stderr
    assert "anthropic-fable" in pretty.stdout
    assert raw.returncode == 0, raw.stderr
    document = json.loads(raw.stdout)
    assert document["source"] == "lb"
    assert document["pools"][0]["source"] == "scoped_marker"


def test_pools_exits_one_when_neither_endpoint_answers(home: Path) -> None:
    result = run("pools", home=home)

    assert result.returncode == 1
    assert "/api/pools unavailable" in result.stderr


def test_doctor_write_against_an_unreachable_lb_exits_one_and_writes_the_alert(home: Path) -> None:
    result = run(
        "doctor",
        "--write",
        home=home,
        extra={"ROUTE_CURSOR_CMD": "/bin/echo cursor-ok", "ROUTE_CODEX_CMD": "/bin/echo codex-ok"},
    )

    assert result.returncode == 1, result.stdout + result.stderr
    state = json.loads((home / ".claude" / "routing-state.json").read_text(encoding="utf-8"))
    assert state["seats"]["opus-seat"]["ok"] is False
    assert state["seats"]["cursor-seat"]["ok"] is True
    failed = {probe["name"] for probe in state["probes"] if not probe["ok"]}
    assert {"lb-health", "lb-pools", "model:claude-opus-5", "model:claude-sonnet-5"} <= failed
    alert = (home / ".claude" / "routing-ALERT").read_text(encoding="utf-8")
    assert "ROUTING DEGRADED" in alert
    assert "lb-health" in alert


def test_doctor_clears_the_alert_when_every_probe_passes(home: Path, tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    pools_fixture(fixtures, {"anthropic-general": "ok", "openai-codex": "ok"})
    write_fixture(fixtures, "health.json", {"status": "ok"})
    write_fixture(fixtures, "api_health.json", {"status": "ok"})
    write_fixture(fixtures, "v1_messages.json", {"id": "msg_probe", "content": []})
    stale = home / ".claude" / "routing-ALERT"
    stale.write_text("ROUTING DEGRADED at 2026-09-18T00:00:00Z\n", encoding="utf-8")

    result = run(
        "doctor",
        "--write",
        home=home,
        fixtures=fixtures,
        extra={"ROUTE_CURSOR_CMD": "/bin/echo cursor-ok", "ROUTE_CODEX_CMD": "/bin/echo codex-ok"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not stale.exists()
    state = json.loads((home / ".claude" / "routing-state.json").read_text(encoding="utf-8"))
    assert all(probe["ok"] for probe in state["probes"])
    assert state["seats"]["opus-seat"]["ok"] is True


def test_doctor_without_write_prints_the_state_and_touches_nothing(home: Path) -> None:
    result = run(
        "doctor",
        home=home,
        extra={"ROUTE_CURSOR_CMD": "/bin/echo cursor-ok", "ROUTE_CODEX_CMD": "/bin/echo codex-ok"},
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["ts"]
    assert not (home / ".claude" / "routing-state.json").exists()
    assert not (home / ".claude" / "routing-ALERT").exists()


def test_learn_proposes_a_demotion_at_ten_failing_closeouts_but_not_at_nine(home: Path, tmp_path: Path) -> None:
    ledger(home, closeouts=9, ok=False)
    nine = run("learn", home=home)
    ledger(home, closeouts=10, ok=False)
    ten = run("learn", home=home)

    assert nine.returncode == 0, nine.stderr
    assert "No demotions proposed" in nine.stdout
    assert ten.returncode == 0, ten.stderr
    assert "demote opus-seat/claude-opus-5" in ten.stdout
    assert "implement" in ten.stdout


def test_learn_does_not_propose_a_demotion_when_the_seat_succeeds(home: Path) -> None:
    ledger(home, closeouts=20, ok=True)

    result = run("learn", home=home)

    assert result.returncode == 0
    assert "No demotions proposed" in result.stdout


def test_learn_apply_writes_the_override_with_reason_evidence_and_timestamp(home: Path, tmp_path: Path) -> None:
    ledger(home, closeouts=10, ok=False)
    table = tmp_path / "table.json"
    table.write_text(TABLE.read_text(encoding="utf-8"), encoding="utf-8")

    applied = run("learn", "--apply", home=home, table=table)
    again = run("learn", "--apply", home=home, table=table)

    assert applied.returncode == 0, applied.stderr
    overrides = json.loads(table.read_text(encoding="utf-8"))["overrides"]
    assert len(overrides) == 1
    override = overrides[0]
    assert override["class"] == "implement"
    assert override["demote"] == {"seat": "opus-seat", "model": "claude-opus-5"}
    assert override["evidence"]["closeouts"] == 10
    assert override["evidence"]["okRate"] == 0.0
    assert override["ts"].endswith("Z")
    assert override["reason"]
    assert again.returncode == 0
    assert len(json.loads(table.read_text(encoding="utf-8"))["overrides"]) == 1


def test_report_joins_dispatches_with_closeouts(home: Path) -> None:
    ledger(home, closeouts=3, ok=False)

    result = run("report", "--days", "2", home=home)

    assert result.returncode == 0, result.stderr
    assert "| implement | opus-seat | claude-opus-5 | 3 | 0 | 3 | 0% | 240 |" in result.stdout


def test_report_on_an_empty_ledger_says_so(home: Path) -> None:
    result = run("report", home=home)

    assert result.returncode == 0
    assert "No dispatches" in result.stdout
