from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path


def load_doctor():
    path = Path(__file__).resolve().parents[2] / "clients" / "agent-defs-doctor"
    loader = importlib.machinery.SourceFileLoader("agent_defs_doctor_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def definition(extra: str = "", description: str = "Does work") -> str:
    return f"---\nname: worker\ndescription: {description}\n{extra}---\n# Body\nExact body.\n"


def test_real_shapes_support_folded_description_and_multiline_planner_tools(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "planner.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nname: planner\ndescription: >-\n  Plans a change\n  without editing.\n"
        "model: claude-planner\ntools: [\n  Read,\n  Glob,\n  Grep,\n]\nmetadata:\n  owner: alex\n---\nBody\n"
    )

    result = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", state_dir=tmp_path / "state")

    assert result["summary"] == {"checked": 1, "repaired": 0, "errors": 0, "unresolved": 0}


def test_repair_normalizes_safe_forms_preserving_body_metadata_and_permissions(tmp_path: Path) -> None:
    doctor = load_doctor()
    root = tmp_path / "home"
    agent = root / ".claude" / "agents" / "worker.md"
    agent.parent.mkdir(parents=True)
    original = definition("owner: team\ntools: Read, Write, Bash\n")
    agent.write_text(original)
    agent.chmod(0o640)

    result = doctor.run_check(cwd=tmp_path / "repo", user_root=root, repair=True, state_dir=tmp_path / "state")

    assert result["summary"] == {"checked": 1, "repaired": 1, "errors": 0, "unresolved": 0}
    repaired = agent.read_text()
    assert "owner: team\n" in repaired
    assert "model: inherit\n" in repaired
    assert 'tools: ["Read", "Write", "Bash"]\n' in repaired
    assert repaired.endswith("---\n# Body\nExact body.\n")
    backup = Path(result["files"][0]["backup"])
    assert backup.read_text() == original
    assert agent.stat().st_mode & 0o777 == 0o640


def test_missing_inheritance_is_valid_but_can_be_normalized(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "implicit.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(definition())

    inspected = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", state_dir=tmp_path / "state")
    repaired = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", repair=True, state_dir=tmp_path / "state")

    assert inspected["files"][0]["status"] == "normalizable"
    assert inspected["summary"]["unresolved"] == 0
    assert repaired["summary"]["repaired"] == 1
    assert "model: inherit\ntools: '*'\n---\n" in agent.read_text()


def test_missing_model_and_multiline_empty_tools_repair_before_body(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "empty-flow.md"
    agent.parent.mkdir(parents=True)
    body = "# Body\nDo not move or alter this.\n"
    agent.write_text("---\nname: empty\ndescription: Empty tools\ntools: [\n]\n---\n" + body)

    result = doctor.run_check(
        cwd=tmp_path,
        user_root=tmp_path / "home",
        repair=True,
        state_dir=tmp_path / "state",
    )

    assert result["summary"]["repaired"] == 1
    assert agent.read_text() == (
        "---\nname: empty\ndescription: Empty tools\ntools: '*'\nmodel: inherit\n---\n" + body
    )


def test_empty_tools_does_not_consume_or_confuse_block_list(tmp_path: Path) -> None:
    doctor = load_doctor()
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    empty = agents / "empty.md"
    block = agents / "block.md"
    empty.write_text(definition("model: opus\ntools:\n"))
    block.write_text(definition("model: sonnet\ntools:\n  - Read\n  - Write\n"))

    result = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", repair=True, state_dir=tmp_path / "state")

    assert "tools: '*'\n" in empty.read_text()
    assert "tools:\n  - Read\n  - Write\n" in block.read_text()
    assert not Path(str(block) + ".bak").exists()
    assert result["summary"]["repaired"] == 1


def test_malformed_yaml_is_reported_and_never_written(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "bad.md"
    agent.parent.mkdir(parents=True)
    original = b"---\nname: bad\nthis is not yaml\ndescription: broken\n---\nbody\n"
    agent.write_bytes(original)

    result = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", repair=True, state_dir=tmp_path / "state")

    assert result["summary"]["errors"] == 1
    assert agent.read_bytes() == original
    assert not Path(str(agent) + ".bak").exists()


def test_symlink_repair_backs_up_and_replaces_target_without_breaking_link(tmp_path: Path) -> None:
    doctor = load_doctor()
    target = tmp_path / "shared.md"
    target.write_text(definition("tools: All tools\n"))
    target.chmod(0o600)
    agent = tmp_path / ".claude" / "agents" / "linked.md"
    agent.parent.mkdir(parents=True)
    agent.symlink_to(target)

    result = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", repair=True, state_dir=tmp_path / "state")

    assert agent.is_symlink()
    assert "tools: '*'" in target.read_text()
    assert Path(result["files"][0]["backup"]).parent == target.parent
    assert target.stat().st_mode & 0o777 == 0o600


def test_unknown_model_is_error_but_local_catalog_adds_authoritative_id(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "future.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(definition("model: routed-future\ntools: '*'\n"))
    state = tmp_path / "state"

    unknown = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", state_dir=state)
    catalog = tmp_path / "models.json"
    catalog.write_text(json.dumps({"models": [{"id": "routed-future"}]}))
    known = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home", models_file=catalog, state_dir=state)

    assert unknown["summary"]["errors"] == 1
    assert known["summary"]["errors"] == 0
    assert known["cached"] is False


def test_cache_hits_and_invalidates_on_content_catalog_and_root(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "worker.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(definition("model: opus\ntools: [Read]\n"))
    state = tmp_path / "state"

    first = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home-a", state_dir=state)
    hit = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home-a", state_dir=state)
    agent.write_text(definition("model: sonnet\ntools: [Read]\n"))
    content_miss = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home-a", state_dir=state)
    root_miss = doctor.run_check(cwd=tmp_path, user_root=tmp_path / "home-b", state_dir=state)

    assert first["cached"] is False
    assert hit["cached"] is True
    assert content_miss["cached"] is False
    assert root_miss["cached"] is False


def test_repair_cache_skips_work_then_invalidates_and_repairs_content_change(
    tmp_path: Path, monkeypatch
) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "worker.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(definition("tools: All tools\n"))
    arguments = {
        "cwd": tmp_path,
        "user_root": tmp_path / "home",
        "repair": True,
        "state_dir": tmp_path / "state",
    }

    first = doctor.run_check(**arguments)
    original_repair = doctor.repair_file
    monkeypatch.setattr(
        doctor,
        "repair_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache hit must skip repair")),
    )
    hit = doctor.run_check(**arguments)
    monkeypatch.setattr(doctor, "repair_file", original_repair)
    agent.write_text(definition("model: opus\ntools:\n"))
    changed = doctor.run_check(**arguments)

    assert first["summary"]["repaired"] == 1
    assert hit["cached"] is True
    assert hit["summary"]["repaired"] == 0
    assert all("repaired" not in item and "backup" not in item for item in hit["files"])
    assert changed["cached"] is False
    assert changed["summary"]["repaired"] == 1
    assert "tools: '*'" in agent.read_text()


def test_nested_cwd_finds_nearest_repository_agent_definitions(tmp_path: Path) -> None:
    doctor = load_doctor()
    (tmp_path / ".git").mkdir()
    agent = tmp_path / ".claude" / "agents" / "repo.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(definition("model: claude-fable-5-1[1m]\ntools: [Read]\n"))
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)

    result = doctor.run_check(
        cwd=nested,
        user_root=tmp_path / "home",
        state_dir=tmp_path / "state",
    )

    assert result["summary"]["checked"] == 1
    assert result["files"][0]["path"] == str(agent)
    assert result["files"][0]["status"] == "ok"


def test_malformed_flow_with_trailing_comment_is_never_repaired(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "bad-flow.md"
    agent.parent.mkdir(parents=True)
    original = definition("model: opus\ntools: [Read,, Write] # malformed\n")
    agent.write_text(original)

    result = doctor.run_check(
        cwd=tmp_path,
        user_root=tmp_path / "home",
        repair=True,
        state_dir=tmp_path / "state",
    )

    assert result["summary"]["errors"] == 1
    assert agent.read_text() == original
    assert not Path(str(agent) + ".bak").exists()


def test_quiet_cli_is_silent_on_clean_cache_hit(tmp_path: Path, capsys) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "worker.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(definition("model: gpt-5.6-sol-xhigh\ntools: '*'\n"))
    arguments = [
        "--quiet",
        "--cwd",
        str(tmp_path),
        "--user-root",
        str(tmp_path / "home"),
        "--state-dir",
        str(tmp_path / "state"),
    ]

    assert doctor.main(arguments) == 0
    assert capsys.readouterr().out == ""
    assert doctor.main(arguments) == 0
    assert capsys.readouterr().out == ""


def test_literal_description_and_quoted_scalars_are_valid(tmp_path: Path) -> None:
    doctor = load_doctor()
    agent = tmp_path / ".claude" / "agents" / "quoted.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nname: 'quoted-agent'\ndescription: |\n  First line.\n  Second line.\n"
        'model: "fable"\ntools: ["Read", \'Write\']\n---\nbody\n'
    )

    result = doctor.inspect_file(agent)

    assert result["status"] == "ok"
