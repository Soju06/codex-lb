from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_versions import (
    assert_project_versions,
    format_beta_changelog,
    is_releasable_conventional_commit,
    latest_beta_tag,
    next_beta_number,
    parse_tag,
    parse_version,
    read_pyproject_version,
    releasable_commits_since,
    tag_targets_head,
    update_project_versions,
)


def write_minimal_release_files(root: Path, version: str = "1.18.2") -> None:
    (root / "app").mkdir(parents=True)
    (root / "frontend").mkdir(parents=True)
    (root / "deploy" / "helm" / "codex-lb").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "codex-lb"\nversion = "{version}"\n', encoding="utf-8")
    (root / "app" / "__init__.py").write_text(
        f'__version__ = "{version}"  # x-release-please-version\n', encoding="utf-8"
    )
    (root / "frontend" / "package.json").write_text(
        json.dumps({"name": "frontend", "version": version}) + "\n", encoding="utf-8"
    )
    (root / "deploy" / "helm" / "codex-lb" / "Chart.yaml").write_text(
        f"apiVersion: v2\nname: codex-lb\nversion: {version}\nappVersion: {version}\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        f'[[package]]\nname = "codex-lb"\nversion = "{version}"\nsource = {{ editable = "." }}\n',
        encoding="utf-8",
    )


def init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, stdout=subprocess.PIPE)


def commit_all(root: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, stdout=subprocess.PIPE)


def append_readme(root: Path, text: str) -> None:
    readme = root / "README.md"
    existing = readme.read_text(encoding="utf-8") if readme.exists() else ""
    readme.write_text(existing + text + "\n", encoding="utf-8")


def test_parse_stable_and_beta_versions() -> None:
    stable = parse_tag("v1.19.0")
    assert stable.version == "1.19.0"
    assert stable.channel == "stable"
    assert stable.pypi_version == "1.19.0"
    assert not stable.is_prerelease

    beta = parse_version("1.19.0-beta.2")
    assert beta.tag == "v1.19.0-beta.2"
    assert beta.base == "1.19.0"
    assert beta.channel == "beta"
    assert beta.serial == 2
    assert beta.pypi_version == "1.19.0b2"
    assert beta.is_prerelease


def test_read_pyproject_version_uses_project_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.example]\nversion = "0.0.0"\n\n[project]\nname = "codex-lb"\nversion = "1.19.0"\n',
        encoding="utf-8",
    )

    assert read_pyproject_version(tmp_path) == "1.19.0"


def test_release_metadata_make_latest_outputs() -> None:
    stable = subprocess.run(
        [sys.executable, "-m", "scripts.release_metadata", "--tag", "v1.19.0"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    beta = subprocess.run(
        [sys.executable, "-m", "scripts.release_metadata", "--tag", "v1.19.0-beta.1"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    assert "make_latest=legacy" in stable.stdout
    assert "make_latest=false" in beta.stdout


@pytest.mark.parametrize("bad", ["1.19", "v1.19.0", "1.19.0-beta", "1.19.0-beta.0", "1.19.0-dev.1"])
def test_parse_version_rejects_non_release_spellings(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_version(bad)


def test_update_project_versions_keeps_all_release_files_in_sync(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path)

    update_project_versions(tmp_path, "1.19.0-beta.1")

    assert_project_versions(tmp_path, "1.19.0-beta.1")
    package_version = json.loads((tmp_path / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    assert package_version == "1.19.0-beta.1"


def test_assert_project_versions_accepts_pep440_uv_lock_prerelease(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path, "1.20.0-beta.3")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "codex-lb"\nversion = "1.20.0b3"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )

    assert_project_versions(tmp_path, "1.20.0-beta.3")


@pytest.mark.parametrize(
    ("path", "writer"),
    [
        (
            "pyproject.toml",
            lambda root: (root / "pyproject.toml").write_text(
                '[project]\nname = "codex-lb"\nversion = "1.20.0b3"\n',
                encoding="utf-8",
            ),
        ),
        (
            "frontend/package.json",
            lambda root: (root / "frontend" / "package.json").write_text(
                json.dumps({"name": "frontend", "version": "1.20.0b3"}) + "\n", encoding="utf-8"
            ),
        ),
        (
            "deploy/helm/codex-lb/Chart.yaml",
            lambda root: (root / "deploy" / "helm" / "codex-lb" / "Chart.yaml").write_text(
                "apiVersion: v2\nname: codex-lb\nversion: 1.20.0b3\nappVersion: 1.20.0b3\n",
                encoding="utf-8",
            ),
        ),
    ],
)
def test_assert_project_versions_rejects_pep440_beta_in_non_uv_lock_release_files(
    tmp_path: Path, path: str, writer
) -> None:
    write_minimal_release_files(tmp_path, "1.20.0-beta.3")
    writer(tmp_path)

    with pytest.raises(ValueError):
        assert_project_versions(tmp_path, "1.20.0-beta.3")


def test_next_beta_number_uses_existing_tags(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path)
    init_git_repo(tmp_path)
    subprocess.run(["git", "tag", "v1.19.0-beta.1"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v1.19.0-beta.3"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v1.20.0-beta.9"], cwd=tmp_path, check=True)

    latest = latest_beta_tag(tmp_path, "1.19.0")
    assert latest is not None
    assert latest.tag == "v1.19.0-beta.3"
    assert next_beta_number(tmp_path, "1.19.0") == 4


def test_tag_targets_head_detects_covered_beta_merge(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path, "1.19.0-beta.1")
    init_git_repo(tmp_path)
    subprocess.run(["git", "tag", "v1.19.0-beta.1"], cwd=tmp_path, check=True)

    assert tag_targets_head(tmp_path, "v1.19.0-beta.1")

    append_readme(tmp_path, "new feature")
    commit_all(tmp_path, "feat: new feature")

    assert not tag_targets_head(tmp_path, "v1.19.0-beta.1")


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        ("feat: add upstream proxy controls", "", True),
        ("fix(proxy): preserve image tools", "", True),
        ("perf(db): add latest usage index", "", True),
        ("deps: bump starlette", "", True),
        ("refactor(api)!: replace legacy envelope", "", True),
        ("chore: release v1.20.0-beta.2", "", False),
        ("ci: skip unrelated CI jobs", "", False),
        ("docs(readme): clarify model availability", "", False),
        ("refactor(api): split helpers", "", False),
        ("custom subject", "", False),
        ("chore: update config", "BREAKING CHANGE: config field removed", True),
    ],
)
def test_is_releasable_conventional_commit(subject: str, body: str, expected: bool) -> None:
    assert is_releasable_conventional_commit(subject, body) is expected


def test_releasable_commits_since_ignores_post_beta_ci_only_commit(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path, "1.20.0-beta.2")
    init_git_repo(tmp_path)
    subprocess.run(["git", "tag", "v1.20.0-beta.2"], cwd=tmp_path, check=True)

    append_readme(tmp_path, "ci-only")
    commit_all(tmp_path, "ci: skip unrelated CI jobs")

    assert releasable_commits_since(tmp_path, "v1.20.0-beta.2") == []
    assert format_beta_changelog(tmp_path, "v1.20.0-beta.2") == (
        "No releasable Conventional Commits since `v1.20.0-beta.2`."
    )


def test_releasable_commits_since_keeps_post_beta_feature_commit(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path, "1.20.0-beta.2")
    init_git_repo(tmp_path)
    subprocess.run(["git", "tag", "v1.20.0-beta.2"], cwd=tmp_path, check=True)

    append_readme(tmp_path, "ci-only")
    commit_all(tmp_path, "ci: skip unrelated CI jobs")
    append_readme(tmp_path, "feature")
    commit_all(tmp_path, "feat(proxy): add upstream proxy controls")

    releasable = releasable_commits_since(tmp_path, "v1.20.0-beta.2")
    assert [commit.subject for commit in releasable] == ["feat(proxy): add upstream proxy controls"]
    assert "- feat(proxy): add upstream proxy controls (" in format_beta_changelog(tmp_path, "v1.20.0-beta.2")


def test_prepare_beta_release_skips_ci_only_commit_after_latest_beta(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path, "1.20.0-beta.2")
    init_git_repo(tmp_path)
    subprocess.run(["git", "tag", "v1.20.0-beta.2"], cwd=tmp_path, check=True)

    append_readme(tmp_path, "ci-only")
    commit_all(tmp_path, "ci: skip unrelated CI jobs")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.prepare_beta_release",
            "--root",
            str(tmp_path),
            "--base-version",
            "1.20.0",
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
    )

    assert "should_create=false" in result.stdout
    assert "reason=v1.20.0-beta.2 already covers all releasable commits" in result.stdout
    assert_project_versions(tmp_path, "1.20.0-beta.2")


def test_prepare_beta_release_creates_next_beta_for_feature_commit(tmp_path: Path) -> None:
    write_minimal_release_files(tmp_path, "1.20.0-beta.2")
    init_git_repo(tmp_path)
    subprocess.run(["git", "tag", "v1.20.0-beta.2"], cwd=tmp_path, check=True)

    append_readme(tmp_path, "feature")
    commit_all(tmp_path, "feat(proxy): add upstream proxy controls")
    changelog_path = tmp_path / "beta-changelog.md"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.prepare_beta_release",
            "--root",
            str(tmp_path),
            "--base-version",
            "1.20.0",
            "--changelog-path",
            str(changelog_path),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
    )

    assert "version=1.20.0-beta.3" in result.stdout
    assert "previous_tag=v1.20.0-beta.2" in result.stdout
    assert_project_versions(tmp_path, "1.20.0-beta.3")
    assert "feat(proxy): add upstream proxy controls" in changelog_path.read_text(encoding="utf-8")
