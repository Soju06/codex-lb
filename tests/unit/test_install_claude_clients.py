from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-claude-clients.sh"
POLICY_INSTALLER = ROOT / "config" / "coding-agents" / "install-policy.py"
MANAGED_DESIGNER = ROOT / "config" / "coding-agents" / "agents" / "frontend-designer.md"
DESIGNER_OWNER = Path(".agent-lb/managed/coding-agents/frontend-designer")
MANAGED_PLANNER = ROOT / "config" / "coding-agents" / "agents" / "planner.md"
PLANNER_OWNER = Path(".agent-lb/managed/coding-agents/planner")
MARKER_BLOCK = (
    "<!-- agent-lb:coding-agent-routing:start -->\n"
    "## Fable/Codex Routing\n\nretired ccdex adapter\n"
    "<!-- agent-lb:coding-agent-routing:end -->\n"
)


def test_installer_preview_is_non_mutating(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    policy_dir = tmp_path / "policy"
    user_home = tmp_path / "home"
    current = user_home / ".agent-lb" / "clients" / "current"
    result = subprocess.run(
        [str(INSTALLER), "--print"],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "AGENT_LB_CLIENT_BIN_DIR": str(bin_dir),
            "AGENT_LB_POLICY_DIR": str(policy_dir),
            "AGENT_LB_USER_HOME": str(user_home),
        },
    )

    assert not bin_dir.exists()
    assert not policy_dir.exists()
    assert not user_home.exists()
    assert f"copy client bundle to {current.parent}/versions/" in result.stdout
    assert f"link {bin_dir}/cc -> {current}/clients/cc" in result.stdout
    assert f"link {bin_dir}/fable -> {current}/clients/fable" in result.stdout
    assert f"link {bin_dir}/opus -> {current}/clients/opus" in result.stdout
    assert f"link {bin_dir}/claude-lb-launch -> {current}/clients/claude-lb-launch" in result.stdout
    assert f"link {bin_dir}/agent-defs-doctor -> {current}/clients/agent-defs-doctor" in result.stdout
    assert f"link {policy_dir}/coding-agents -> {current}/config/coding-agents" in result.stdout
    assert f"would converge managed routing configuration in {user_home}/.claude/CLAUDE.md" in result.stdout
    assert "remove retired ccdex artifacts (clients, hook, MCP registration)" in result.stdout
    assert f"link {bin_dir}/ccdex" not in result.stdout


def test_installer_converges_links_and_removes_retired_artifacts(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    policy_dir = tmp_path / "policy"
    user_home = tmp_path / "home"
    bin_dir.mkdir()
    original = bin_dir / "cc"
    original.write_text("original wrapper\n")
    (bin_dir / "ccdex").symlink_to(ROOT / "clients" / "ccdex")
    (bin_dir / "ccdex-worker-mcp").symlink_to(ROOT / "clients" / "ccdex-worker-mcp")
    calls = tmp_path / "claude-calls"
    claude = tmp_path / "claude"
    claude.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{calls}"\n')
    claude.chmod(0o755)
    hook = user_home / ".claude" / "hooks" / "ccdex-gpt-only.sh"
    hook.parent.mkdir(parents=True)
    hook.symlink_to(ROOT / "config" / "coding-agents" / "install-policy.py")
    codex_doc = user_home / ".codex" / "AGENTS.md"
    codex_doc.parent.mkdir(parents=True)
    codex_doc.write_text(f"# Codex\n\nkeep-codex\n\n{MARKER_BLOCK}")
    env = {
        **os.environ,
        "AGENT_LB_CLIENT_BIN_DIR": str(bin_dir),
        "AGENT_LB_POLICY_DIR": str(policy_dir),
        "AGENT_LB_CLAUDE_BIN": str(claude),
        "AGENT_LB_USER_HOME": str(user_home),
    }

    subprocess.run([str(INSTALLER)], check=True, env=env, capture_output=True, text=True)
    current = user_home / ".agent-lb" / "clients" / "current"
    first_version = current.resolve()
    subprocess.run([str(INSTALLER)], check=True, env=env, capture_output=True, text=True)

    assert current.resolve() == first_version
    assert list((current.parent / "versions").iterdir()) == [first_version]
    assert (bin_dir / "cc.pre-agent-lb").read_text() == "original wrapper\n"
    assert (bin_dir / "cc").is_symlink()
    assert (bin_dir / "cc").resolve() == first_version / "clients" / "cc"
    for name in ("fable", "opus", "claude-lb-launch", "agent-defs-doctor"):
        assert (bin_dir / name).is_symlink()
        assert (bin_dir / name).resolve() == first_version / "clients" / name
        assert (bin_dir / name).read_bytes() == (ROOT / "clients" / name).read_bytes()
    for name in ("ccdex", "ccdex-worker-mcp"):
        assert not (bin_dir / name).exists()
    assert not hook.exists()
    assert (policy_dir / "coding-agents").is_symlink()
    assert (policy_dir / "coding-agents").resolve() == first_version / "config" / "coding-agents"
    claude_doc_text = (user_home / ".claude" / "CLAUDE.md").read_text()
    assert claude_doc_text.count("agent-lb:coding-agent-routing:start") == 1
    codex_text = codex_doc.read_text()
    assert "keep-codex" in codex_text
    assert "agent-lb:coding-agent-routing" not in codex_text
    assert calls.read_text().splitlines() == [
        "mcp remove --scope user ccdex-worker",
        "mcp remove --scope user ccdex-worker",
    ]
    dry_run = subprocess.run(
        [str(bin_dir / "cc")],
        check=True,
        capture_output=True,
        text=True,
        env={
            **env,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CLAUDE_LB_DISABLE": "1",
            "CLAUDE_LB_DRY_RUN": "1",
        },
    )
    assert "--autocompact 1m" in dry_run.stdout
    assert "--model claude-opus-5" not in dry_run.stdout

    subprocess.run([str(INSTALLER), "--uninstall"], check=True, env=env, capture_output=True, text=True)
    for name in ("cc", "fable", "opus", "claude-lb-launch", "agent-defs-doctor"):
        assert not (bin_dir / name).exists()
    assert not (policy_dir / "coding-agents").exists()
    assert (bin_dir / "cc.pre-agent-lb").read_text() == "original wrapper\n"


def _isolated_install_env(tmp_path: Path) -> dict[str, str]:
    claude = tmp_path / "claude"
    claude.write_text("#!/usr/bin/env bash\nexit 0\n")
    claude.chmod(0o755)
    return {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "AGENT_LB_USER_HOME": str(tmp_path / "home"),
        "AGENT_LB_CLIENT_BIN_DIR": str(tmp_path / "bin"),
        "AGENT_LB_POLICY_DIR": str(tmp_path / "policy"),
        "AGENT_LB_CLIENT_RUNTIME_DIR": str(tmp_path / "runtime"),
        "AGENT_LB_CLAUDE_BIN": str(claude),
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
    }


def _copy_client_source(tmp_path: Path) -> Path:
    source = tmp_path / "source checkout"
    for directory in ("clients", "config/coding-agents"):
        shutil.copytree(ROOT / directory, source / directory, ignore=shutil.ignore_patterns("__pycache__"))
    (source / "scripts").mkdir()
    shutil.copy2(INSTALLER, source / "scripts" / INSTALLER.name)
    return source


def test_installed_commands_and_uninstall_work_without_source_checkout(tmp_path: Path) -> None:
    source = _copy_client_source(tmp_path)
    env = _isolated_install_env(tmp_path)
    subprocess.run([str(source / "scripts" / INSTALLER.name)], check=True, env=env, capture_output=True)
    current = tmp_path / "runtime" / "current"
    bundle = current.resolve()
    source.rename(tmp_path / "unavailable-source")

    assert not any(path.is_symlink() for path in bundle.rglob("*"))
    for name in ("cc", "fable", "opus", "claude-lb-launch"):
        result = subprocess.run(
            [str(tmp_path / "bin" / name), "--version"],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**env, "CLAUDE_LB_DISABLE": "1", "CLAUDE_LB_DRY_RUN": "1"},
        )
        assert "--version" in result.stdout
    for doctor in (tmp_path / "bin" / "agent-defs-doctor", bundle / "clients" / "opus-runtime-doctor"):
        subprocess.run([str(doctor), "--help"], check=True, env=env, capture_output=True)
    assert (tmp_path / "policy" / "coding-agents" / "ROUTING.md").read_bytes() == (
        ROOT / "config" / "coding-agents" / "ROUTING.md"
    ).read_bytes()
    subprocess.run(
        [str(current / "scripts" / INSTALLER.name), "--print"], check=True, env=env, capture_output=True
    )

    replacement = tmp_path / "bin" / "opus"
    replacement.unlink()
    replacement.write_text("local replacement\n")
    subprocess.run(
        [str(current / "scripts" / INSTALLER.name), "--uninstall"], check=True, env=env, capture_output=True
    )
    assert replacement.read_text() == "local replacement\n"
    assert not (tmp_path / "bin" / "cc").is_symlink()
    assert not (tmp_path / "policy" / "coding-agents").is_symlink()
    assert bundle.is_dir()


def test_client_upgrade_retains_previous_bundle_and_preserves_unmanaged_links(tmp_path: Path) -> None:
    source = _copy_client_source(tmp_path)
    env = _isolated_install_env(tmp_path)
    command = [str(source / "scripts" / INSTALLER.name)]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    legacy = tmp_path / "unavailable-repo" / "clients" / "cc"
    (bin_dir / "cc").symlink_to(legacy)
    subprocess.run(command, check=True, env=env, capture_output=True)
    current = tmp_path / "runtime" / "current"
    previous = current.resolve()
    previous_bytes = (previous / "clients" / "fable").read_bytes()
    client = source / "clients" / "fable"
    client.write_bytes(previous_bytes + b"\n")

    subprocess.run(command, check=True, env=env, capture_output=True)

    assert (bin_dir / "cc.pre-agent-lb").readlink() == legacy
    assert current.resolve() != previous
    assert (previous / "clients" / "fable").read_bytes() == previous_bytes
    assert (bin_dir / "fable").read_bytes() == previous_bytes + b"\n"
    assert len(list((current.parent / "versions").iterdir())) == 2
    subprocess.run(command, check=True, env=env, capture_output=True)
    assert len(list((current.parent / "versions").iterdir())) == 2


def test_client_backup_conflict_does_not_mutate_earlier_targets_or_configuration(tmp_path: Path) -> None:
    env = _isolated_install_env(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("cc", "agent-defs-doctor", "agent-defs-doctor.pre-agent-lb"):
        (bin_dir / name).write_text(f"keep {name}\n")
    before = {path: path.read_bytes() for path in bin_dir.iterdir()}

    result = subprocess.run([str(INSTALLER)], env=env, capture_output=True, text=True)

    assert result.returncode != 0
    assert "backup already exists" in result.stderr
    assert {path: path.read_bytes() for path in bin_dir.iterdir()} == before
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "runtime").exists()


def test_policy_installer_migrates_legacy_sections_and_preserves_unrelated_configuration(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_doc = home / ".claude" / "CLAUDE.md"
    codex_doc = home / ".codex" / "AGENTS.md"
    settings_path = home / ".claude" / "settings.json"
    claude_doc.parent.mkdir(parents=True)
    codex_doc.parent.mkdir(parents=True)
    claude_doc.write_text(
        "# Global\n\nkeep-before\n\n"
        "## Orchestration — Fable architects, the fleet executes\n\n"
        "legacy sonnet routing\n\n## Project rules\n\nkeep-after\n"
    )
    codex_doc.write_text(f"# Codex\n\nkeep-codex\n\n{MARKER_BLOCK}\n## Safety\n\nkeep-safety\n")
    settings_path.write_text(
        """{
  "model": "opus",
  "env": {"KEEP": "yes"},
  "permissions": {"allow": ["keep-me"]},
  "hooks": {
    "PostToolUse": [{"matcher": "Skill", "hooks": [{"type": "command", "command": "keep-node-hook"}]}],
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [
        {"type": "command", "command": "$HOME/.claude/hooks/ccdex-gpt-only.sh"},
        {"type": "command", "command": "keep-safety-hook"}
      ]},
      {"matcher": "Agent|Workflow", "hooks": [
        {"type": "command", "command": "$HOME/.claude/hooks/ccdex-gpt-only.sh"}
      ]}
    ]
  }
}
"""
    )

    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)
    first = {path: path.read_bytes() for path in (claude_doc, codex_doc, settings_path)}
    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)

    assert {path: path.read_bytes() for path in first} == first
    assert "keep-before" in claude_doc.read_text() and "keep-after" in claude_doc.read_text()
    assert "legacy sonnet routing" not in claude_doc.read_text()
    assert claude_doc.read_text().count("agent-lb:coding-agent-routing:start") == 1
    codex_text = codex_doc.read_text()
    assert "keep-codex" in codex_text and "keep-safety" in codex_text
    assert "agent-lb:coding-agent-routing" not in codex_text

    settings = json.loads(settings_path.read_text())
    assert settings["model"] == "fable"
    assert settings["effortLevel"] == "high"
    assert settings["env"] == {"KEEP": "yes"}
    assert settings["permissions"] == {"allow": ["keep-me"]}
    assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "keep-node-hook"
    commands = [
        (group["matcher"], hook_config["command"])
        for group in settings["hooks"]["PreToolUse"]
        for hook_config in group["hooks"]
    ]
    assert commands == [("Bash", "keep-safety-hook")]


def test_policy_installer_preflight_failure_does_not_mutate_any_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_doc = home / ".claude" / "CLAUDE.md"
    codex_doc = home / ".codex" / "AGENTS.md"
    settings_path = home / ".claude" / "settings.json"
    claude_doc.parent.mkdir(parents=True)
    codex_doc.parent.mkdir(parents=True)
    claude_doc.write_text("# Keep\n<!-- agent-lb:coding-agent-routing:start -->\nbroken\n")
    codex_doc.write_text("# Keep codex\n")
    settings_path.write_text('{"model": "keep"}\n')
    before = {path: path.read_bytes() for path in (claude_doc, codex_doc, settings_path)}

    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "malformed or duplicate" in result.stderr
    assert {path: path.read_bytes() for path in before} == before


def test_policy_installer_invalid_json_does_not_mutate_documents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_doc = home / ".claude" / "CLAUDE.md"
    codex_doc = home / ".codex" / "AGENTS.md"
    settings_path = home / ".claude" / "settings.json"
    claude_doc.parent.mkdir(parents=True)
    codex_doc.parent.mkdir(parents=True)
    claude_doc.write_text("# Keep claude\n")
    codex_doc.write_text("# Keep codex\n")
    settings_path.write_text("{broken")
    before = {path: path.read_bytes() for path in (claude_doc, codex_doc, settings_path)}

    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "invalid JSON" in result.stderr
    assert {path: path.read_bytes() for path in before} == before


def test_policy_installer_installs_frontend_designer_and_converges_idempotently(tmp_path: Path) -> None:
    home = tmp_path / "home"
    designer = home / ".claude" / "agents" / "frontend-designer.md"
    owner = home / DESIGNER_OWNER

    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)
    first_content = designer.read_bytes()
    checkpoints = home / ".agent-lb" / "config-checkpoints" / "coding-agents"
    first_checkpoints = sorted(checkpoints.iterdir())
    second = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert first_content == MANAGED_DESIGNER.read_bytes()
    assert b"\nmodel: fable\n" in first_content
    assert designer.read_bytes() == first_content
    assert owner.read_text() == "agent-lb:frontend-designer:v1\n"
    assert sorted(checkpoints.iterdir()) == first_checkpoints
    assert "already converged" in second.stdout

    subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert not designer.exists()
    assert not owner.exists()


def test_policy_installer_previews_and_checkpoints_designer_replacement(tmp_path: Path) -> None:
    home = tmp_path / "home"
    agents = home / ".claude" / "agents"
    designer = agents / "frontend-designer.md"
    unrelated = agents / "keep-me.md"
    agents.mkdir(parents=True)
    original_designer = b"---\nname: frontend-designer\nmodel: fable\n---\n"
    designer.write_bytes(original_designer)
    unrelated.write_text("keep this agent\n")

    preview = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--print"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"would converge managed routing configuration in {designer}" in preview.stdout
    assert designer.read_bytes() == original_designer
    assert unrelated.read_text() == "keep this agent\n"
    assert not (home / ".agent-lb").exists()

    installed = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        check=True,
        capture_output=True,
        text=True,
    )
    checkpoint = Path(
        next(
            line.removeprefix("checkpoint ")
            for line in installed.stdout.splitlines()
            if line.startswith("checkpoint ")
        )
    )

    assert designer.read_bytes() == MANAGED_DESIGNER.read_bytes()
    assert (checkpoint / ".claude" / "agents" / "frontend-designer.md").read_bytes() == original_designer
    assert unrelated.read_text() == "keep this agent\n"


def test_policy_installer_uninstall_preserves_customized_designer(tmp_path: Path) -> None:
    home = tmp_path / "home"
    designer = home / ".claude" / "agents" / "frontend-designer.md"
    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)
    designer.write_text(designer.read_text() + "\nLocal customization.\n")
    customized = designer.read_bytes()

    preview = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--print", "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"would preserve customized {designer}" in preview.stdout
    assert f"preserved customized {designer}" in result.stdout
    assert designer.read_bytes() == customized


def test_policy_installer_uninstall_preserves_empty_customized_designer(tmp_path: Path) -> None:
    home = tmp_path / "home"
    designer = home / ".claude" / "agents" / "frontend-designer.md"
    designer.parent.mkdir(parents=True)
    designer.touch()

    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"preserved unmanaged {designer}" in result.stdout
    assert designer.exists()
    assert designer.read_bytes() == b""


def test_policy_installer_uninstall_preserves_identical_unmanaged_designer(tmp_path: Path) -> None:
    home = tmp_path / "home"
    designer = home / ".claude" / "agents" / "frontend-designer.md"
    designer.parent.mkdir(parents=True)
    designer.write_bytes(MANAGED_DESIGNER.read_bytes())

    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"preserved unmanaged {designer}" in result.stdout
    assert designer.read_bytes() == MANAGED_DESIGNER.read_bytes()


def test_policy_installer_installs_planner_and_converges_idempotently(tmp_path: Path) -> None:
    home = tmp_path / "home"
    planner = home / ".claude" / "agents" / "planner.md"
    owner = home / PLANNER_OWNER

    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)
    first_content = planner.read_bytes()
    checkpoints = home / ".agent-lb" / "config-checkpoints" / "coding-agents"
    first_checkpoints = sorted(checkpoints.iterdir())
    second = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert first_content == MANAGED_PLANNER.read_bytes()
    assert b"\nmodel: claude-planner\n" in first_content
    assert b"\neffort: high\n" in first_content
    assert planner.read_bytes() == first_content
    assert owner.read_text() == "agent-lb:planner:v1\n"
    assert sorted(checkpoints.iterdir()) == first_checkpoints
    assert "already converged" in second.stdout

    subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert not planner.exists()
    assert not owner.exists()


def test_policy_installer_previews_and_checkpoints_planner_replacement(tmp_path: Path) -> None:
    home = tmp_path / "home"
    agents = home / ".claude" / "agents"
    planner = agents / "planner.md"
    unrelated = agents / "keep-me.md"
    agents.mkdir(parents=True)
    original_planner = b"---\nname: planner\nmodel: fable\n---\n"
    planner.write_bytes(original_planner)
    unrelated.write_text("keep this agent\n")

    preview = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--print"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"would converge managed routing configuration in {planner}" in preview.stdout
    assert planner.read_bytes() == original_planner
    assert unrelated.read_text() == "keep this agent\n"
    assert not (home / ".agent-lb").exists()

    installed = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        check=True,
        capture_output=True,
        text=True,
    )
    checkpoint = Path(
        next(
            line.removeprefix("checkpoint ")
            for line in installed.stdout.splitlines()
            if line.startswith("checkpoint ")
        )
    )

    assert planner.read_bytes() == MANAGED_PLANNER.read_bytes()
    assert (checkpoint / ".claude" / "agents" / "planner.md").read_bytes() == original_planner
    assert unrelated.read_text() == "keep this agent\n"


def test_policy_installer_uninstall_preserves_customized_planner(tmp_path: Path) -> None:
    home = tmp_path / "home"
    planner = home / ".claude" / "agents" / "planner.md"
    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)
    planner.write_text(planner.read_text() + "\nLocal customization.\n")
    customized = planner.read_bytes()

    preview = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--print", "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"would preserve customized {planner}" in preview.stdout
    assert f"preserved customized {planner}" in result.stdout
    assert planner.read_bytes() == customized


def test_policy_installer_uninstall_preserves_identical_unmanaged_planner(tmp_path: Path) -> None:
    home = tmp_path / "home"
    planner = home / ".claude" / "agents" / "planner.md"
    planner.parent.mkdir(parents=True)
    planner.write_bytes(MANAGED_PLANNER.read_bytes())

    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home), "--uninstall"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"preserved unmanaged {planner}" in result.stdout
    assert planner.read_bytes() == MANAGED_PLANNER.read_bytes()
