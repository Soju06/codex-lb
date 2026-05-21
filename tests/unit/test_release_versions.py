from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_versions import (
    assert_project_versions,
    latest_beta_tag,
    next_beta_number,
    parse_tag,
    parse_version,
    read_pyproject_version,
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

    (tmp_path / "README.md").write_text("new feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "feat: new feature"], cwd=tmp_path, check=True, stdout=subprocess.PIPE)

    assert not tag_targets_head(tmp_path, "v1.19.0-beta.1")
