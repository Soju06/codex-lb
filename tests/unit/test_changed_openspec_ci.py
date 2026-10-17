from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/validate_changed_openspec.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD")


def change(repo: Path, slug: str, name: str = "proposal.md") -> Path:
    path = repo / "openspec/changes" / slug / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{slug}\n")
    return path


def setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    change(repo, "untouched-invalid")
    commit(repo)
    return repo


def run_gate(repo: Path, event_name: str, event: dict, fail: str = "") -> subprocess.CompletedProcess[str]:
    event_file = repo.parent / "event.json"
    event_file.write_text(json.dumps(event))
    bin_dir = repo.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    validator = bin_dir / "npx"
    validator.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "print('VALIDATE ' + ' '.join(sys.argv[1:]))\n"
        "sys.exit(1 if os.environ.get('FAIL_CHANGE') in sys.argv[1:] else 0)\n"
    )
    validator.chmod(0o755)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(event_file),
            "FAIL_CHANGE": fail,
        },
        capture_output=True,
        text=True,
    )


def test_pr_uses_merge_base_and_event_head_in_merge_checkout(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    ancestor = git(repo, "rev-parse", "HEAD")
    change(repo, "target-only-invalid")
    base = commit(repo)
    git(repo, "checkout", "-qb", "pr", ancestor)
    change(repo, "touched")
    change(repo, "touched", "notes with spaces.md")
    head = commit(repo)
    git(repo, "merge", "--no-edit", base)

    result = run_gate(repo, "pull_request", {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}})

    assert result.returncode == 0, result.stderr
    validations = [line for line in result.stdout.splitlines() if line.startswith("VALIDATE ")]
    assert validations == [
        "VALIDATE --yes @fission-ai/openspec@1.11.0 validate --type change --strict --no-interactive -- touched"
    ]


def test_push_and_merge_queue_validate_the_event_range(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    change(repo, "new-change")
    head = commit(repo)
    for event_name, event in (
        ("push", {"before": base, "after": head}),
        ("merge_group", {"merge_group": {"base_sha": base, "head_sha": head}}),
    ):
        result = run_gate(repo, event_name, event, fail="new-change")
        assert "--type change --strict --no-interactive -- new-change" in result.stdout
        assert result.returncode != 0
        assert "untouched-invalid" not in result.stdout


def test_new_branch_push_validates_all_tracked_active_changes(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    result = run_gate(repo, "push", {"before": "0" * 40, "after": git(repo, "rev-parse", "HEAD")})
    assert result.returncode == 0, result.stderr
    assert "--type change --strict --no-interactive -- untouched-invalid" in result.stdout


def test_renames_deletions_and_archives_select_surviving_folders(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    deleted = change(repo, "deleted")
    archived = change(repo, "archived")
    proposal = change(repo, "partial")
    change(repo, "partial", "tasks.md")
    moved = change(repo, "source", "delta.md")
    change(repo, "source")
    base = commit(repo)
    deleted.unlink()
    archived.rename(change(repo, "archive/2026-09-10-archived"))
    proposal.unlink()
    moved.rename(change(repo, "destination", "delta.md"))
    head = commit(repo)
    git(repo, "clean", "-fd")  # Match a fresh Actions checkout: Git does not retain empty directories.

    result = run_gate(repo, "push", {"before": base, "after": head})

    assert result.returncode == 0, result.stderr
    validations = [line.split(" -- ")[1] for line in result.stdout.splitlines() if "VALIDATE " in line]
    assert validations == ["destination", "partial", "source"]


def test_missing_history_fails_without_validating_unrelated_changes(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    result = run_gate(repo, "push", {"before": "a" * 40, "after": git(repo, "rev-parse", "HEAD")})
    assert result.returncode != 0
    assert "VALIDATE " not in result.stdout


def test_no_changed_active_folders_succeeds(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("unrelated")
    head = commit(repo)
    result = run_gate(repo, "push", {"before": base, "after": head})
    assert result.returncode == 0, result.stderr
    assert "VALIDATE " not in result.stdout


def test_option_like_folder_is_a_positional_change_name(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    change(repo, "--help")
    head = commit(repo)
    result = run_gate(repo, "push", {"before": base, "after": head})
    assert result.returncode == 0, result.stderr
    assert "--type change --strict --no-interactive -- --help" in result.stdout
