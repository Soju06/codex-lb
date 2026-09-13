from __future__ import annotations

import fcntl
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str):
    path = ROOT / "clients" / name
    module_name = name.replace("-", "_") + "_test"
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


def _event(**value):
    return json.dumps(value, separators=(",", ":"))


def valid_stream(nonce: str, *, second_model: str = "claude-opus-5", terminal: bool = True) -> str:
    lines: list[str] = []
    for number, (agent_type, inputs, model) in enumerate(
        (
            ("general-purpose", {"subagent_type": "general-purpose", "model": "opus"}, "claude-opus-5"),
            ("opus-seat", {"subagent_type": "opus-seat"}, second_model),
        ),
        start=1,
    ):
        agent_id = f"agent-{number}"
        read_id = f"read-{number}"
        lines.append(
            _event(
                type="assistant",
                message={
                    "model": "claude-fable-5-1",
                    "content":[{"type": "tool_use", "id": agent_id, "name": "Agent", "input": inputs}],
                },
                parent_tool_use_id=None,
            )
        )
        lines.append(
            _event(
                type="assistant",
                message={
                    "model": model,
                    "content":[{"type": "tool_use", "id": read_id, "name": "Read", "input": {}}],
                },
                parent_tool_use_id=agent_id,
            )
        )
        lines.append(
            _event(
                type="user",
                message={"content": [{"type": "tool_result", "tool_use_id": read_id, "content": nonce}]},
                parent_tool_use_id=agent_id,
            )
        )
        lines.append(
            _event(
                type="assistant",
                message={"model": model, "content": [{"type": "text", "text": f"{nonce} {model}[1m]"}]},
                parent_tool_use_id=agent_id,
            )
        )
        lines.append(
            _event(
                type="user",
                message={"content": [{"type": "tool_result", "tool_use_id": agent_id, "content": nonce}]},
                parent_tool_use_id=None,
                tool_use_result={"status": "completed", "resolvedModel": f"{model}[1m]", "agentType": agent_type},
            )
        )
    if terminal:
        lines.append(_event(type="result", subtype="success", is_error=False, modelUsage={"forgery": {}}))
    return "\n".join(lines) + "\n"


def test_analyze_stream_accepts_parent_linked_runtime_evidence() -> None:
    doctor = load_script("opus-runtime-doctor")
    status, error, evidence = doctor.analyze_stream(valid_stream("owned-nonce"), "owned-nonce", "claude-opus-5")

    assert (status, error) == ("PASS", None)
    assert evidence["agent_call_count"] == 2
    assert evidence["child_nonce_reads"] == 2
    assert evidence["matched_child_models"] == 2
    assert evidence["terminal_success"] is True


@pytest.mark.parametrize(
    ("stream", "error"),
    [
        ("not-json\n", "malformed_stream"),
        (valid_stream("n", terminal=False), "missing_terminal_success"),
        (valid_stream("n", second_model="claude-sonnet-4-6"), "unexpected_child_model"),
    ],
)
def test_analyze_stream_fails_closed(stream: str, error: str) -> None:
    doctor = load_script("opus-runtime-doctor")
    assert doctor.analyze_stream(stream, "n", "claude-opus-5")[:2] == ("FAIL", error)


def test_parent_prose_and_model_usage_cannot_forge_child_evidence() -> None:
    doctor = load_script("opus-runtime-doctor")
    nonce = "forged-nonce"
    stream = "\n".join(
        [
            _event(
                type="assistant",
                message={
                    "model": "claude-fable-5-1",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"a-{number}",
                            "name": "Agent",
                            "input": {"subagent_type": kind, **({"model": "opus"} if number == 1 else {})},
                        }
                    ],
                },
            )
            for number, kind in ((1, "general-purpose"), (2, "opus-seat"))
        ]
        + [
            _event(
                type="assistant",
                message={"model": "claude-fable-5-1", "content": [{"type": "text", "text": nonce}]},
                modelUsage={"claude-opus-5": {}},
            ),
            _event(type="result", subtype="success", is_error=False, modelUsage={"claude-opus-5": {}}),
        ]
    )

    status, error, _ = doctor.analyze_stream(stream, nonce, "claude-opus-5")
    assert status == "FAIL"
    assert error in {"agent_calls_not_sequential", "missing_successful_nonce_result"}


def test_failed_child_read_result_does_not_count_as_nonce_evidence() -> None:
    doctor = load_script("opus-runtime-doctor")
    stream = valid_stream("owned-nonce").replace(
        '"tool_use_id":"read-1","content":"owned-nonce"',
        '"tool_use_id":"read-1","content":"owned-nonce","is_error":true',
        1,
    )

    assert doctor.analyze_stream(stream, "owned-nonce", "claude-opus-5")[:2] == (
        "FAIL",
        "missing_child_nonce_read",
    )


def test_run_doctor_repairs_and_retries_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))
    outcomes = iter(
        [
            doctor.ProbeOutcome("FAIL", "unexpected_child_model", {}, 0.1),
            doctor.ProbeOutcome("PASS", None, {"matched_child_models": 2}, 0.2),
        ]
    )
    retries: list[bool] = []
    repairs: list[bool] = []
    monkeypatch.setattr(
        doctor,
        "run_probe",
        lambda *, canonical_retry=False: (retries.append(canonical_retry), next(outcomes))[1],
    )
    monkeypatch.setattr(
        doctor,
        "run_definition_repair",
        lambda *, quiet=True: (repairs.append(quiet), {"status": "ok"})[1],
    )

    assert doctor.run_doctor(nightly=False, repair=False, quiet=True) == 0
    assert retries == [False, True]
    assert repairs == [True]
    receipt = json.loads((tmp_path / "doctor.json").read_text())
    assert receipt["result"] == "PASS"
    assert [attempt["status"] for attempt in receipt["attempts"]] == ["FAIL", "PASS"]


def test_unresolved_definition_repair_fails_even_when_probe_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        doctor,
        "run_definition_repair",
        lambda *, quiet=True: {"status": "failed", "returncode": 1},
    )
    monkeypatch.setattr(doctor, "run_probe", lambda **kwargs: doctor.ProbeOutcome("PASS", None, {}, 0.1))

    assert doctor.run_doctor(nightly=False, repair=True, quiet=True) == 1
    receipt = json.loads((tmp_path / "doctor.json").read_text())
    assert receipt["result"] == "FAIL"
    assert receipt["definition_repairs"] == [
        {"returncode": 1, "status": "failed"},
        {"returncode": 1, "status": "failed"},
    ]
    assert len(receipt["attempts"]) == 2


def test_transient_definition_failure_heals_on_single_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))
    repairs = iter([{"status": "failed", "returncode": 1}, {"status": "ok", "returncode": 0}])
    monkeypatch.setattr(doctor, "run_definition_repair", lambda **kwargs: next(repairs))
    monkeypatch.setattr(doctor, "run_probe", lambda **kwargs: doctor.ProbeOutcome("PASS", None, {}, 0.1))

    assert doctor.run_doctor(nightly=True, repair=True, quiet=True) == 0
    receipt = json.loads((tmp_path / "doctor.json").read_text())
    assert len(receipt["definition_repairs"]) == 2
    assert len(receipt["attempts"]) == 2


def test_probe_timeout_kills_owned_process_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))

    class Process:
        pid = 43210
        returncode = None
        calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("probe", timeout)
            return (
                _event(type="system", subtype="init", model="claude-fable-5-1", session_id="safe-session") + "\n",
                "Operation not permitted; secret details omitted",
            )

    process = Process()
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(doctor.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(doctor.os, "killpg", lambda pid, sig: killed.append((pid, sig)))

    outcome = doctor.run_probe()
    assert outcome.error_class == "timeout"
    assert killed == [(process.pid, doctor.signal.SIGKILL)]
    assert outcome.evidence == {
        "event_count": 1,
        "malformed_lines": 0,
        "init_model": "claude-fable-5-1",
        "session_id": "safe-session",
        "last_event_type": "system",
        "last_event_subtype": "init",
        "terminal_subtype": None,
        "parent_models": [],
        "child_models": [],
        "stderr_tags": ["operation_not_permitted"],
    }


def test_stream_diagnostics_sanitizes_models_and_classifies_stderr() -> None:
    doctor = load_script("opus-runtime-doctor")
    stdout = "\n".join(
        [
            _event(type="system", subtype="init", model="claude-fable-5-1[1m]", session_id="session-1"),
            _event(type="assistant", message={"model": "claude-opus-5", "content": []}),
            _event(
                type="assistant",
                message={"model": "unsafe model with spaces SECRET", "content": []},
                parent_tool_use_id="agent-1",
            ),
            _event(type="assistant", message={"model": "claude-opus-5", "content": []}, parent_tool_use_id="agent-1"),
            _event(type="result", subtype="error", is_error=True),
        ]
    )
    evidence = doctor.stream_diagnostics(stdout, "OAuth keychain rate limit connection refused SECRET")

    assert evidence["init_model"] == "claude-fable-5-1[1m]"
    assert evidence["session_id"] == "session-1"
    assert evidence["parent_models"] == ["claude-opus-5"]
    assert evidence["child_models"] == ["claude-opus-5"]
    assert evidence["terminal_subtype"] == "error"
    assert evidence["stderr_tags"] == ["auth", "keychain", "connection", "rate_limit"]
    assert "SECRET" not in json.dumps(evidence)


def test_probe_command_disables_slash_commands() -> None:
    doctor = load_script("opus-runtime-doctor")
    command = doctor._probe_command(Path("/safe/fable"), "safe prompt", "session")
    assert "--disable-slash-commands" in command


def test_schedule_claims_before_detach_and_gates_six_hours(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))
    spawns: list[list[str]] = []
    monkeypatch.setattr(doctor.subprocess, "Popen", lambda command, **kwargs: spawns.append(command))

    assert doctor.schedule() == 0
    assert (tmp_path / "opus-doctor-scheduled.json").exists()
    assert doctor.schedule() == 0
    assert len(spawns) == 1


@pytest.mark.parametrize("malformed", ["null", "[]", '"scalar"', '{"claimed_at":"soon"}'])
def test_schedule_tolerates_valid_json_with_wrong_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, malformed: str
) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))
    (tmp_path / "opus-doctor-scheduled.json").write_text(malformed)
    spawns: list[list[str]] = []
    monkeypatch.setattr(doctor.subprocess, "Popen", lambda command, **kwargs: spawns.append(command))

    assert doctor.schedule() == 0
    assert len(spawns) == 1


def test_schedule_removes_fresh_claim_on_definite_spawn_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(doctor.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no")))

    assert doctor.schedule() == 1
    assert not (tmp_path / "opus-doctor-scheduled.json").exists()


def test_manual_probe_lock_prevents_concurrency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_STATE_DIR", str(tmp_path))
    lock = (tmp_path / "opus-doctor.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(doctor, "run_probe", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not probe")))

    assert doctor.run_doctor(nightly=False, repair=False, quiet=True) == 75


def test_notification_intent_is_persisted_and_ambiguous_send_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doctor = load_script("opus-runtime-doctor")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_NOTIFY", "/safe/notify-alex.sh")
    receipt = {
        "run_id": "run-1",
        "finished_at": 1,
        "mode": "nightly",
        "result": "FAIL",
        "attempts": [],
    }
    sends: list[int] = []

    def ambiguous(*args, **kwargs):
        sends.append(1)
        assert json.loads((tmp_path / "doctor.json").read_text())["notification"]["status"] == "intent"
        raise subprocess.TimeoutExpired("notify", 30)

    monkeypatch.setattr(doctor.subprocess, "run", ambiguous)
    doctor._notify_failure(receipt, tmp_path)
    assert receipt["notification"]["status"] == "unknown"
    doctor._notify_failure(receipt, tmp_path)
    assert receipt["notification"]["status"] == "deduplicated"
    assert len(sends) == 1


def test_launcher_doctor_modes_propagate_without_building_claude_command(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_script("claude-lb-launch")
    invocations: list[bool] = []
    monkeypatch.setattr(launcher, "run_opus_doctor", lambda nightly=False: (invocations.append(nightly), 19)[1])
    monkeypatch.setattr(
        launcher,
        "build_command",
        lambda argv: (_ for _ in ()).throw(AssertionError("must not forward")),
    )

    monkeypatch.setattr(launcher.sys, "argv", ["claude-lb-launch", "--doctor"])
    with pytest.raises(SystemExit, match="19"):
        launcher.main()
    monkeypatch.setattr(launcher.sys, "argv", ["claude-lb-launch", "--doctor-nightly"])
    with pytest.raises(SystemExit, match="19"):
        launcher.main()
    assert invocations == [False, True]


def test_launcher_probe_require_flag_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_script("claude-lb-launch")
    launcher.CCGPT_MODE = False
    monkeypatch.setenv("CLAUDE_LB_REQUIRE", "1")
    monkeypatch.setenv("CLAUDE_LB_DOCTOR_SKIP", "1")
    monkeypatch.setattr(launcher, "prepare_interactive_endpoint", lambda: False)
    monkeypatch.setattr(launcher.sys, "argv", ["claude-lb-launch"])

    with pytest.raises(SystemExit, match="1"):
        launcher.main()


def test_installer_prints_pinned_nightly_job(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "AGENT_LB_DOCTOR_HOME": str(tmp_path),
        "AGENT_LB_DOCTOR_LAUNCHER": "/internal/agent-lb/clients/claude-lb-launch",
    }
    completed = subprocess.run(
        [str(ROOT / "scripts" / "install-opus-doctor.sh"), "--print"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "com.agent-lb.opus-doctor" in completed.stdout
    assert "/internal/agent-lb/clients/claude-lb-launch" in completed.stdout
    assert "<integer>3</integer>" in completed.stdout
    assert "<integer>20</integer>" in completed.stdout


def test_installer_rejects_external_volume_launcher(tmp_path: Path) -> None:
    launcher = tmp_path / "claude-lb-launch"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    completed = subprocess.run(
        [str(ROOT / "scripts" / "install-opus-doctor.sh")],
        env={**os.environ, "AGENT_LB_DOCTOR_HOME": str(tmp_path), "AGENT_LB_DOCTOR_LAUNCHER": str(launcher)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "refusing external-volume launcher" in completed.stderr
