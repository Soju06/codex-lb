"""Release version helpers for codex-lb release workflows.

The GitHub workflows intentionally use this stdlib-only module so that release
metadata can be validated before project dependencies are installed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_VERSION_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?:-(?P<channel>alpha|beta|rc)\.(?P<serial>[1-9]\d*))?$")
_TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.[1-9]\d*)?)$")
_CONVENTIONAL_SUBJECT_RE = re.compile(r"^(?P<type>[a-z][a-z0-9-]*)(?:\([^)]+\))?(?P<breaking>!)?: (?P<description>.+)$")
_RELEASABLE_CONVENTIONAL_TYPES = frozenset({"deps", "feat", "fix", "perf", "revert"})
_PEP440_PRERELEASE_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?P<channel>a|b|rc)(?P<serial>[1-9]\d*)$")
_PEP440_CHANNELS = {
    "a": "alpha",
    "b": "beta",
    "rc": "rc",
}


@dataclass(frozen=True)
class CommitMessage:
    sha: str
    subject: str
    body: str


@dataclass(frozen=True)
class ReleaseVersion:
    version: str
    base: str
    channel: str
    serial: int | None

    @property
    def is_prerelease(self) -> bool:
        return self.channel != "stable"

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def pypi_version(self) -> str:
        """Return the normalized PEP 440 spelling expected in PyPI output."""

        if self.channel == "stable":
            return self.version
        if self.channel == "alpha":
            return f"{self.base}a{self.serial}"
        if self.channel == "beta":
            return f"{self.base}b{self.serial}"
        if self.channel == "rc":
            return f"{self.base}rc{self.serial}"
        raise AssertionError(f"unexpected release channel: {self.channel}")


def parse_version(version: str) -> ReleaseVersion:
    match = _VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError(f"invalid release version {version!r}; expected X.Y.Z or X.Y.Z-(alpha|beta|rc).N")
    channel = match.group("channel") or "stable"
    serial = int(match.group("serial")) if match.group("serial") else None
    return ReleaseVersion(version=version, base=match.group("base"), channel=channel, serial=serial)


def parse_tag(tag: str) -> ReleaseVersion:
    match = _TAG_RE.fullmatch(tag)
    if not match:
        raise ValueError(f"invalid release tag {tag!r}; expected vX.Y.Z or vX.Y.Z-(alpha|beta|rc).N")
    return parse_version(match.group("version"))


def read_pyproject_version_text(text: str) -> str:
    data = tomllib.loads(text)
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError("could not find [project] table in pyproject.toml")
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("could not find [project] version in pyproject.toml")
    return version


def read_pyproject_version(root: Path) -> str:
    return read_pyproject_version_text((root / "pyproject.toml").read_text(encoding="utf-8"))


def _replace_once(text: str, pattern: str, replacement: str, *, path: str) -> str:
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"expected exactly one replacement in {path}, got {count}")
    return new_text


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def update_project_versions(root: Path, version: str) -> None:
    """Update all release-please managed version files to *version*.

    This deliberately does not modify .github/release-please-manifest.json:
    beta releases are prerelease snapshots of the next stable train, while the
    stable train remains owned by release-please.
    """

    parse_version(version)

    pyproject = root / "pyproject.toml"
    _write_text(
        pyproject,
        _replace_once(
            pyproject.read_text(encoding="utf-8"),
            r'^version = "[^"]+"$',
            f'version = "{version}"',
            path=str(pyproject),
        ),
    )

    init_py = root / "app" / "__init__.py"
    _write_text(
        init_py,
        _replace_once(
            init_py.read_text(encoding="utf-8"),
            r'^__version__ = "[^"]+"',
            f'__version__ = "{version}"',
            path=str(init_py),
        ),
    )

    package_json = root / "frontend" / "package.json"
    package_data = json.loads(package_json.read_text(encoding="utf-8"))
    package_data["version"] = version
    _write_text(package_json, json.dumps(package_data, indent=2, ensure_ascii=False) + "\n")

    chart = root / "deploy" / "helm" / "codex-lb" / "Chart.yaml"
    chart_text = chart.read_text(encoding="utf-8")
    chart_text = _replace_once(chart_text, r"^version: .*$", f"version: {version}", path=str(chart))
    chart_text = _replace_once(chart_text, r"^appVersion: .*$", f"appVersion: {version}", path=str(chart))
    _write_text(chart, chart_text)

    uv_lock = root / "uv.lock"
    uv_text = uv_lock.read_text(encoding="utf-8")
    uv_text, count = re.subn(
        r'(\[\[package\]\]\nname = "codex-lb"\nversion = ")[^"]+("\nsource = \{ editable = "\." \})',
        rf"\g<1>{version}\2",
        uv_text,
        count=1,
    )
    if count != 1:
        raise ValueError("expected exactly one codex-lb package entry in uv.lock")
    _write_text(uv_lock, uv_text)


def read_project_versions(root: Path) -> dict[str, str]:
    package_data = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    chart_text = (root / "deploy" / "helm" / "codex-lb" / "Chart.yaml").read_text(encoding="utf-8")
    uv_text = (root / "uv.lock").read_text(encoding="utf-8")

    def find(pattern: str, text: str, name: str) -> str:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if not match:
            raise ValueError(f"could not find {name}")
        return match.group(1)

    return {
        "pyproject.toml": read_pyproject_version(root),
        "app/__init__.py": find(
            r'^__version__ = "([^"]+)"',
            (root / "app" / "__init__.py").read_text(encoding="utf-8"),
            "app version",
        ),
        "frontend/package.json": package_data["version"],
        "deploy/helm/codex-lb/Chart.yaml version": find(r"^version: (.+)$", chart_text, "chart version"),
        "deploy/helm/codex-lb/Chart.yaml appVersion": find(r"^appVersion: (.+)$", chart_text, "chart appVersion"),
        "uv.lock": find(
            r'\[\[package\]\]\nname = "codex-lb"\nversion = "([^"]+)"\nsource = \{ editable = "\." \}',
            uv_text,
            "uv.lock codex-lb version",
        ),
    }


def canonical_release_version(value: str) -> str:
    match = _PEP440_PRERELEASE_RE.fullmatch(value)
    if match is None:
        return value
    channel = _PEP440_CHANNELS[match.group("channel")]
    return f"{match.group('base')}-{channel}.{match.group('serial')}"


def canonical_release_version_for_file(name: str, value: str) -> str:
    if name == "uv.lock":
        return canonical_release_version(value)
    return value


def assert_project_versions(root: Path, expected_version: str) -> None:
    expected = canonical_release_version(expected_version)
    mismatches = {
        name: actual
        for name, actual in read_project_versions(root).items()
        if canonical_release_version_for_file(name, actual) != canonical_release_version_for_file(name, expected)
    }
    if mismatches:
        detail = ", ".join(f"{name}={actual!r}" for name, actual in sorted(mismatches.items()))
        raise ValueError(f"release version drift: expected {expected_version!r}; {detail}")


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def is_releasable_conventional_commit(subject: str, body: str = "") -> bool:
    """Return whether a commit should open/update a release-please-style beta PR.

    The beta channel should not advance for workflow-only noise such as
    ``ci: ...`` after the previous beta tag. Match release-please's semver
    intent rather than GitHub's broader generated release notes: feature, fix,
    perf, dependency, revert, and breaking-change commits are releasable.
    """

    match = _CONVENTIONAL_SUBJECT_RE.fullmatch(subject.strip())
    if not match:
        return False
    if match.group("breaking"):
        return True
    if "BREAKING CHANGE:" in body or "BREAKING-CHANGE:" in body:
        return True
    return match.group("type") in _RELEASABLE_CONVENTIONAL_TYPES


def commit_messages_since(root: Path, ref: str) -> list[CommitMessage]:
    """Return commits after *ref* in oldest-to-newest order."""

    proc = run_git(root, "log", "--reverse", "--format=%H%x00%s%x00%b%x1e", f"{ref}..HEAD")
    commits: list[CommitMessage] = []
    for record in proc.stdout.rstrip("\x1e\n").split("\x1e"):
        if not record.strip():
            continue
        parts = record.split("\x00", 2)
        if len(parts) != 3:
            raise ValueError(f"unexpected git log record while reading commits since {ref!r}")
        sha, subject, body = parts
        commits.append(CommitMessage(sha=sha, subject=subject, body=body))
    return commits


def releasable_commits_since(root: Path, ref: str) -> list[CommitMessage]:
    return [
        commit
        for commit in commit_messages_since(root, ref)
        if is_releasable_conventional_commit(commit.subject, commit.body)
    ]


def latest_stable_tag(root: Path) -> str | None:
    proc = run_git(root, "tag", "--merged", "HEAD", "--sort=-v:refname")
    stable_tag = re.compile(r"^v\d+\.\d+\.\d+$")
    return next((tag for tag in proc.stdout.splitlines() if stable_tag.fullmatch(tag)), None)


def format_beta_changelog(root: Path, ref: str) -> str:
    commits = releasable_commits_since(root, ref)
    if not commits:
        return f"No releasable Conventional Commits since `{ref}`."

    lines = [f"- {commit.subject} ({commit.sha[:7]})" for commit in commits]
    return "\n".join(lines)


def discover_release_please_base_version(root: Path) -> str:
    """Read the next stable version from the release-please PR branch."""

    candidates = ["origin/release-please--branches--main", "release-please--branches--main"]
    last_error = ""
    for ref in candidates:
        proc = run_git(root, "show", f"{ref}:pyproject.toml", check=False)
        if proc.returncode != 0:
            last_error = proc.stderr.strip()
            continue
        try:
            release = parse_version(read_pyproject_version_text(proc.stdout))
        except ValueError as exc:
            raise ValueError(f"{ref}:pyproject.toml does not contain a valid project version") from exc
        if release.is_prerelease:
            raise ValueError(f"release-please branch version must be stable, got {release.version!r}")
        return release.version
    raise ValueError(
        "could not discover next stable version from release-please PR branch; "
        "run Release Please first or pass --base-version." + (f" Last git error: {last_error}" if last_error else "")
    )


def latest_beta_tag(root: Path, base_version: str) -> ReleaseVersion | None:
    parse_version(base_version)
    proc = run_git(root, "tag", "--list", f"v{base_version}-beta.*")
    latest: ReleaseVersion | None = None
    for tag in proc.stdout.splitlines():
        try:
            parsed = parse_tag(tag.strip())
        except ValueError:
            continue
        if parsed.base != base_version or parsed.channel != "beta" or parsed.serial is None:
            continue
        if latest is None or parsed.serial > (latest.serial or 0):
            latest = parsed
    return latest


def next_beta_number(root: Path, base_version: str) -> int:
    latest = latest_beta_tag(root, base_version)
    return (latest.serial + 1) if latest and latest.serial is not None else 1


def tag_targets_head(root: Path, tag: str) -> bool:
    tag_sha = run_git(root, "rev-parse", f"{tag}^{{commit}}").stdout.strip()
    head_sha = run_git(root, "rev-parse", "HEAD").stdout.strip()
    return tag_sha == head_sha


def write_github_outputs(values: Mapping[str, str | int | bool]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
