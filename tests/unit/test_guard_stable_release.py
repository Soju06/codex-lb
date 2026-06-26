from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.unit.test_guard_beta_release import event_file, git, init_repo_with_beta_commit, update_project_versions


def run_stable_guard(project_root: Path, repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in {"GITHUB_EVENT_PATH", "GITHUB_HEAD_REF"}}
    return subprocess.run(
        [sys.executable, "-m", "scripts.guard_stable_release", "--root", str(repo_root), *args],
        cwd=project_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_stable_guard_rejects_release_please_pr_missing_uv_lock_update(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo_with_beta_commit(repo, version="1.20.0-beta.3")
    base = git(repo, "rev-parse", "HEAD")

    update_project_versions(repo, "1.20.0")
    (repo / "uv.lock").write_text(
        '[[package]]\nname = "codex-lb"\nversion = "1.20.0-beta.3"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "chore(main): release 1.20.0")
    sha = git(repo, "rev-parse", "HEAD")
    event = event_file(tmp_path, head_ref="release-please--branches--main", head_sha=sha, body="")

    result = run_stable_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        base,
        "--head-ref",
        "release-please--branches--main",
        "--event-path",
        str(event),
    )

    assert result.returncode == 1
    assert "Stable release-managed version files must agree" in result.stderr
    assert "uv.lock='1.20.0-beta.3'" in result.stderr
    assert "pyproject.toml='1.20.0'" in result.stderr


def test_stable_guard_accepts_consistent_release_please_stable_pr(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo_with_beta_commit(repo, version="1.20.0-beta.3")
    base = git(repo, "rev-parse", "HEAD")

    update_project_versions(repo, "1.20.0")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "chore(main): release 1.20.0")
    sha = git(repo, "rev-parse", "HEAD")
    event = event_file(tmp_path, head_ref="release-please--branches--main", head_sha=sha, body="")

    result = run_stable_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        base,
        "--head-ref",
        "release-please--branches--main",
        "--event-path",
        str(event),
    )

    assert result.returncode == 0, result.stderr
    assert "Stable release guard passed for 1.20.0" in result.stdout


def test_stable_guard_rejects_stable_promotion_from_non_release_please_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo_with_beta_commit(repo, version="1.20.0-beta.3")
    base = git(repo, "rev-parse", "HEAD")

    update_project_versions(repo, "1.20.0")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "chore(main): release 1.20.0")
    sha = git(repo, "rev-parse", "HEAD")
    event = event_file(tmp_path, head_ref="fix/manual-stable-promotion", head_sha=sha, body="")

    result = run_stable_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        base,
        "--head-ref",
        "fix/manual-stable-promotion",
        "--event-path",
        str(event),
    )

    assert result.returncode == 1
    assert "Stable release promotions must come from the release-please branch" in result.stderr
    assert "Actual head branch: fix/manual-stable-promotion" in result.stderr


def test_stable_guard_accepts_merge_queue_without_head_ref_after_pr_gate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo_with_beta_commit(repo, version="1.20.0-beta.3")
    base = git(repo, "rev-parse", "HEAD")

    update_project_versions(repo, "1.20.0")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "chore(main): release 1.20.0")

    result = run_stable_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        base,
        "--head-ref",
        "",
    )

    assert result.returncode == 0, result.stderr
    assert "No PR head branch available" in result.stdout
    assert "Stable release guard passed for 1.20.0" in result.stdout


def test_stable_guard_allows_non_promotion_metadata_repair_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo_with_beta_commit(repo, version="1.20.0-beta.3")
    update_project_versions(repo, "1.20.0")
    (repo / "uv.lock").write_text(
        '[[package]]\nname = "codex-lb"\nversion = "1.20.0-beta.3"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    git(repo, "add", ".")
    git(repo, "commit", "-m", "chore(main): release 1.20.0 with stale lock")
    base = git(repo, "rev-parse", "HEAD")

    (repo / "uv.lock").write_text(
        '[[package]]\nname = "codex-lb"\nversion = "1.20.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )
    git(repo, "add", "uv.lock")
    git(repo, "commit", "-m", "fix(release): restore uv lock version")

    result = run_stable_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        base,
        "--head-ref",
        "fix/stable-release-artifact-gate",
    )

    assert result.returncode == 0, result.stderr
    assert "No stable release promotion detected" in result.stdout
