from __future__ import annotations

from pathlib import Path

from scripts.check_repo_operability import check_repository


def _make_valid_fixture(root: Path) -> None:
    (root / ".agents").mkdir()
    (root / "bin").mkdir()
    (root / "AGENTS.md").write_text(
        "Canonical commands: bin/setup bin/test bin/dev bin/logs bin/worktree\n",
        encoding="utf-8",
    )
    (root / "ARCHITECTURE.md").write_text("# Architecture\n", encoding="utf-8")
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    (root / ".claude").symlink_to(".agents", target_is_directory=True)
    commands = {
        "setup": "#!/bin/sh\nbun run build\n",
        "test": "#!/bin/sh\n",
        "dev": '#!/bin/sh\nHOST=127.0.0.1\nLOG_DIR="$ROOT/.local/logs"\n',
        "logs": "#!/bin/sh\n",
        "worktree": "#!/bin/sh\n",
        "check-operability": "#!/bin/sh\n",
    }
    for name, content in commands.items():
        path = root / "bin" / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)


def test_operability_guard_accepts_canonical_fixture(tmp_path: Path) -> None:
    _make_valid_fixture(tmp_path)
    assert check_repository(tmp_path) == []


def test_operability_guard_rejects_deliberate_fixture_violation(tmp_path: Path) -> None:
    _make_valid_fixture(tmp_path)
    dev = tmp_path / "bin/dev"
    dev.write_text('#!/bin/sh\nexec codex-lb --host 0.0.0.0\nLOG_DIR="$ROOT/.local/logs"\n', encoding="utf-8")
    dev.chmod(0o755)

    errors = check_repository(tmp_path)

    assert "bin/dev does not pin the service to 127.0.0.1" in errors
    assert "bin/dev contains an all-interface host binding" in errors


def test_operability_guard_rejects_noncanonical_instruction_link(tmp_path: Path) -> None:
    _make_valid_fixture(tmp_path)
    (tmp_path / "CLAUDE.md").unlink()
    (tmp_path / "CLAUDE.md").write_text("duplicate instructions\n", encoding="utf-8")

    assert "instruction compatibility path is not a symlink: CLAUDE.md" in check_repository(tmp_path)


def test_operability_guard_rejects_setup_without_frontend_build(tmp_path: Path) -> None:
    _make_valid_fixture(tmp_path)
    setup = tmp_path / "bin" / "setup"
    setup.write_text("#!/bin/sh\n", encoding="utf-8")
    setup.chmod(0o755)

    assert "bin/setup does not build frontend assets required by bin/dev" in check_repository(tmp_path)
