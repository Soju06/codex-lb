from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_versions import update_project_versions


def write_minimal_release_files(root: Path, version: str = "1.20.0") -> None:
    (root / "app").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    (root / "deploy" / "helm" / "codex-lb").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "codex-lb"\nversion = "{version}"\n', encoding="utf-8")
    (root / "app" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "frontend" / "package.json").write_text(
        json.dumps({"name": "frontend", "version": version}) + "\n",
        encoding="utf-8",
    )
    (root / "deploy" / "helm" / "codex-lb" / "Chart.yaml").write_text(
        f"apiVersion: v2\nname: codex-lb\nversion: {version}\nappVersion: {version}\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        f'[[package]]\nname = "codex-lb"\nversion = "{version}"\nsource = {{ editable = "." }}\n',
        encoding="utf-8",
    )


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def init_repo_with_beta_commit(root: Path, version: str = "1.20.0-beta.3") -> str:
    write_minimal_release_files(root)
    git(root, "init")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "add", ".")
    git(root, "commit", "-m", "init")
    git(root, "branch", "-M", "main")
    update_project_versions(root, version)
    git(root, "add", ".")
    git(root, "commit", "-m", f"chore: release v{version}")
    return git(root, "rev-parse", "HEAD")


def event_file(
    tmp_path: Path,
    *,
    head_ref: str,
    head_sha: str,
    body: str,
    merged: bool = False,
    merge_commit_sha: str | None = None,
    head_repo: str = "Soju06/codex-lb",
    base_repo: str = "Soju06/codex-lb",
) -> Path:
    head_owner, head_name = head_repo.split("/", 1)
    base_owner, base_name = base_repo.split("/", 1)
    event = {
        "repository": {"full_name": base_repo},
        "pull_request": {
            "body": body,
            "head": {
                "ref": head_ref,
                "sha": head_sha,
                "repo": {"full_name": head_repo, "owner": {"login": head_owner}, "name": head_name},
            },
            "base": {"repo": {"full_name": base_repo, "owner": {"login": base_owner}, "name": base_name}},
            "merged": merged,
            "merge_commit_sha": merge_commit_sha,
        },
    }
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event), encoding="utf-8")
    return path


def validation_body(sha: str) -> str:
    return f"""## Release-candidate validation
Validated candidate: {sha}

- [x] Backend/unit/integration gates
- [x] Frontend tests/build
- [x] Wheel/package validation
- [x] Docker/container smoke
- [x] Live upstream/account smoke not required
"""


def run_guard(project_root: Path, repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.guard_beta_release", "--root", str(repo_root), *args],
        cwd=project_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_pr_guard_rejects_beta_metadata_from_noncanonical_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo_with_beta_commit(repo)
    event = event_file(tmp_path, head_ref="fix/pr-938-release-ci", head_sha=sha, body=validation_body(sha))

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        "fix/pr-938-release-ci",
        "--event-path",
        str(event),
    )

    assert result.returncode == 1
    assert "canonical beta release branch" in result.stderr
    assert "release/beta-1.20.0-beta.3" in result.stderr


def test_pr_guard_accepts_dependency_only_release_managed_file_edits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo_with_beta_commit(repo)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "codex-lb"\nversion = "1.20.0-beta.3"\ndependencies = ["aiohttp-socks>=0.10.1"]\n',
        encoding="utf-8",
    )
    (repo / "uv.lock").write_text(
        '[[package]]\nname = "codex-lb"\nversion = "1.20.0-beta.3"\nsource = { editable = "." }\n'
        'dependencies = [{ name = "aiohttp-socks" }]\n',
        encoding="utf-8",
    )
    git(repo, "add", "pyproject.toml", "uv.lock")
    git(repo, "commit", "-m", "deps: add aiohttp socks adapter")
    sha = git(repo, "rev-parse", "HEAD")
    branch = "fix/aiohttp-socks-adapter"
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body="")

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        branch,
        "--event-path",
        str(event),
    )

    assert result.returncode == 0, result.stderr
    assert "No release-managed version files changed" in result.stdout


def test_pr_guard_rejects_inconsistent_release_managed_beta_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_minimal_release_files(repo)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    git(repo, "branch", "-M", "main")
    (repo / "app" / "__init__.py").write_text('__version__ = "1.20.0-beta.3"\n', encoding="utf-8")
    git(repo, "add", "app/__init__.py")
    git(repo, "commit", "-m", "chore: drift app version to beta")
    sha = git(repo, "rev-parse", "HEAD")
    branch = "fix/pr-938-release-ci"
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body=validation_body(sha))

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        branch,
        "--event-path",
        str(event),
    )

    assert result.returncode == 1
    assert "Release-managed version files must agree" in result.stderr
    assert "app/__init__.py='1.20.0-beta.3'" in result.stderr
    assert "pyproject.toml='1.20.0'" in result.stderr


@pytest.mark.parametrize(
    ("path", "writer"),
    [
        (
            "pyproject.toml",
            lambda repo: (repo / "pyproject.toml").write_text(
                '[project]\nname = "codex-lb"\nversion = "1.20.0b3"\n',
                encoding="utf-8",
            ),
        ),
        (
            "frontend/package.json",
            lambda repo: (repo / "frontend" / "package.json").write_text(
                json.dumps({"name": "frontend", "version": "1.20.0b3"}) + "\n",
                encoding="utf-8",
            ),
        ),
    ],
)
def test_pr_guard_rejects_pep440_beta_version_in_non_lock_release_file_on_beta_base(
    tmp_path: Path, path: str, writer
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_minimal_release_files(repo, version="1.20.0-beta.3")
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    git(repo, "branch", "-M", "main")

    writer(repo)
    git(repo, "add", path)
    git(repo, "commit", "-m", "chore: make version spelling PEP 440")
    sha = git(repo, "rev-parse", "HEAD")
    branch = "dependabot/bad-version-spelling"
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body="")

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        branch,
        "--event-path",
        str(event),
    )

    assert result.returncode == 1
    assert "Release-managed version files must agree before beta release gating" in result.stderr
    assert "1.20.0b3" in result.stderr


def test_pr_guard_accepts_dependency_only_package_json_edits_on_beta_base(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_minimal_release_files(repo, version="1.20.0-beta.3")
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    git(repo, "branch", "-M", "main")

    package_json = repo / "frontend" / "package.json"
    package_json.write_text(
        json.dumps({"name": "frontend", "version": "1.20.0-beta.3", "dependencies": {"radix-ui": "^1.5.0"}}) + "\n",
        encoding="utf-8",
    )
    git(repo, "add", "frontend/package.json")
    git(repo, "commit", "-m", "chore(deps): bump frontend dependencies")
    sha = git(repo, "rev-parse", "HEAD")
    branch = "dependabot/bun/frontend/frontend-minor-patch"
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body="")

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        branch,
        "--event-path",
        str(event),
    )

    assert result.returncode == 0, result.stderr
    assert "No release-managed version files changed" in result.stdout


def test_pr_guard_accepts_pep440_uv_lock_beta_version_on_dependency_edits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_minimal_release_files(repo, version="1.20.0-beta.3")
    (repo / "uv.lock").write_text(
        '[[package]]\nname = "codex-lb"\nversion = "1.20.0b3"\nsource = { editable = "." }\n'
        '\n[[package]]\nname = "starlette"\nversion = "1.3.1"\n',
        encoding="utf-8",
    )
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "init")
    git(repo, "branch", "-M", "main")
    base = git(repo, "rev-parse", "HEAD")

    (repo / "uv.lock").write_text(
        '[[package]]\nname = "codex-lb"\nversion = "1.20.0b3"\nsource = { editable = "." }\n'
        '\n[[package]]\nname = "starlette"\nversion = "1.3.2"\n',
        encoding="utf-8",
    )
    git(repo, "add", "uv.lock")
    git(repo, "commit", "-m", "chore(deps): bump starlette")

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--mode",
        "pr",
        "--base-ref",
        base,
        "--head-ref",
        "dependabot/uv/starlette-1.3.2",
    )

    assert result.returncode == 0, result.stderr
    assert "No release-managed version files changed" in result.stdout


def test_pr_guard_rejects_canonical_beta_pr_without_validation_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo_with_beta_commit(repo)
    branch = "release/beta-1.20.0-beta.3"
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body="## Summary\nRelease beta3")

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        branch,
        "--event-path",
        str(event),
    )

    assert result.returncode == 1
    assert "release-candidate validation evidence" in result.stderr
    assert sha in result.stderr


def test_pr_guard_accepts_validated_canonical_beta_pr(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo_with_beta_commit(repo)
    branch = "release/beta-1.20.0-beta.3"
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body=validation_body(sha))

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        branch,
        "--event-path",
        str(event),
    )

    assert result.returncode == 0, result.stderr
    assert "Beta release PR guard passed" in result.stdout


def test_pr_guard_rejects_forked_canonical_beta_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo_with_beta_commit(repo)
    branch = "release/beta-1.20.0-beta.3"
    event = event_file(
        tmp_path,
        head_ref=branch,
        head_sha=sha,
        body=validation_body(sha),
        head_repo="evil-fork/codex-lb",
    )

    result = run_guard(
        Path(__file__).resolve().parents[2],
        repo,
        "--base-ref",
        "HEAD~1",
        "--head-ref",
        branch,
        "--event-path",
        str(event),
    )

    assert result.returncode == 1
    assert "canonical repository release branch" in result.stderr
    assert "Expected head repository: Soju06/codex-lb" in result.stderr
    assert "Actual head repository: evil-fork/codex-lb" in result.stderr


def test_publish_guard_rejects_stale_candidate_sha_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo_with_beta_commit(repo)
    branch = "release/beta-1.20.0-beta.3"
    stale_sha = "0" * 40
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body=validation_body(stale_sha), merged=True)

    result = run_guard(Path(__file__).resolve().parents[2], repo, "--mode", "publish", "--event-path", str(event))

    assert result.returncode == 1
    assert "validation evidence" in result.stderr
    assert sha in result.stderr


def test_publish_guard_accepts_validated_merged_canonical_beta_pr(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo_with_beta_commit(repo)
    branch = "release/beta-1.20.0-beta.3"
    event = event_file(tmp_path, head_ref=branch, head_sha=sha, body=validation_body(sha), merged=True)

    result = run_guard(Path(__file__).resolve().parents[2], repo, "--mode", "publish", "--event-path", str(event))

    assert result.returncode == 0, result.stderr
    assert "Beta publish guard passed" in result.stdout


def test_publish_guard_rejects_forked_canonical_beta_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sha = init_repo_with_beta_commit(repo)
    branch = "release/beta-1.20.0-beta.3"
    event = event_file(
        tmp_path,
        head_ref=branch,
        head_sha=sha,
        body=validation_body(sha),
        merged=True,
        head_repo="evil-fork/codex-lb",
    )

    result = run_guard(Path(__file__).resolve().parents[2], repo, "--mode", "publish", "--event-path", str(event))

    assert result.returncode == 1
    assert "canonical repository release branch" in result.stderr
    assert "Expected head repository: Soju06/codex-lb" in result.stderr
    assert "Actual head repository: evil-fork/codex-lb" in result.stderr


def test_publish_guard_rejects_merge_tree_that_differs_from_validated_head(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    head_sha = init_repo_with_beta_commit(repo)
    (repo / "README.md").write_text("main advanced after validation\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "fix: advance main after beta validation")
    merge_sha = git(repo, "rev-parse", "HEAD")
    branch = "release/beta-1.20.0-beta.3"
    event = event_file(
        tmp_path,
        head_ref=branch,
        head_sha=head_sha,
        body=validation_body(head_sha),
        merged=True,
        merge_commit_sha=merge_sha,
    )

    result = run_guard(Path(__file__).resolve().parents[2], repo, "--mode", "publish", "--event-path", str(event))

    assert result.returncode == 1
    assert "merge commit tree differs from the validated candidate head" in result.stderr
    assert head_sha in result.stderr
    assert merge_sha in result.stderr
