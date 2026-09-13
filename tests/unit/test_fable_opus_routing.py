from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FABLE = ROOT / "clients" / "fable"


class ExecCalled(Exception):
    pass


def load_fable() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("fable_under_test", str(FABLE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def run_fable(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> tuple[list[str], dict[str, str]]:
    module = load_fable()
    captured: dict[str, object] = {}

    def fake_execv(path: Path, command: list[str]) -> None:
        captured["path"] = path
        captured["command"] = command
        captured["environment"] = dict(os.environ)
        raise ExecCalled

    monkeypatch.setattr(module.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    monkeypatch.setattr(module.os, "execv", fake_execv)
    monkeypatch.setattr(sys, "argv", [str(FABLE), *argv])

    with pytest.raises(ExecCalled):
        module.main()

    assert captured["path"] == ROOT / "clients" / "claude-lb-launch"
    return captured["command"], captured["environment"]


def test_defaults_fable_driver_and_canonical_opus_seat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LB_FABLE_MODEL", raising=False)
    monkeypatch.delenv("AGENT_LB_OPUS_MODEL", raising=False)

    command, environment = run_fable(monkeypatch, ["-p", "hello"])

    assert environment["ANTHROPIC_MODEL"] == "claude-fable-5[1m]"
    assert environment["ANTHROPIC_DEFAULT_FABLE_MODEL"] == "claude-fable-5[1m]"
    assert environment["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-5"
    assert command == [str(ROOT / "clients" / "claude-lb-launch"), "--autocompact", "1m", "-p", "hello"]


def test_nonempty_driver_and_opus_overrides_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FABLE_MODEL", "claude-fable-custom")
    monkeypatch.setenv("AGENT_LB_OPUS_MODEL", "claude-opus-custom")

    _, environment = run_fable(monkeypatch, [])

    assert environment["ANTHROPIC_MODEL"] == "claude-fable-custom"
    assert environment["ANTHROPIC_DEFAULT_FABLE_MODEL"] == "claude-fable-custom"
    assert environment["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-custom"


def test_inherited_fable_poison_cannot_capture_opus_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_OPUS_MODEL", "")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-fable-5")

    _, environment = run_fable(monkeypatch, [])

    assert environment["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-5"


def test_seat_slots_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    seats = {
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "sonnet-seat",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "haiku-seat",
        "CLAUDE_CODE_SUBAGENT_MODEL": "subagent-seat",
    }
    for slot, model in seats.items():
        monkeypatch.setenv(slot, model)

    _, environment = run_fable(monkeypatch, [])

    assert {slot: environment[slot] for slot in seats} == seats


def test_explicit_cli_model_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    command, _ = run_fable(monkeypatch, ["--model", "explicit-model", "-p", "hello"])

    assert command == [
        str(ROOT / "clients" / "claude-lb-launch"),
        "--autocompact",
        "1m",
        "--model",
        "explicit-model",
        "-p",
        "hello",
    ]
