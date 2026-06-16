#!/usr/bin/env python3
"""Guard beta release PRs and publishing against unvalidated candidates.

This script is intentionally stdlib-only so it can run early in GitHub Actions,
before project dependencies are installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from scripts.release_versions import (
    ReleaseVersion,
    parse_version,
    read_project_versions,
    read_pyproject_version,
    read_pyproject_version_text,
)

RELEASE_MANAGED_FILES = (
    "pyproject.toml",
    "app/__init__.py",
    "frontend/package.json",
    "deploy/helm/codex-lb/Chart.yaml",
    "uv.lock",
)

_REQUIRED_EVIDENCE_LABELS = (
    "backend/unit/integration gates",
    "frontend tests/build",
    "wheel/package validation",
    "docker/container smoke",
)

_LIVE_SMOKE_ACCEPTED_LABELS = (
    "live upstream/account smoke",
    "live upstream/account smoke not required",
)

_SHA_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
_PEP440_PRERELEASE_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?P<channel>a|b|rc)(?P<serial>[1-9]\d*)$")
_PEP440_CHANNELS = {
    "a": "alpha",
    "b": "beta",
    "rc": "rc",
}


class GuardError(RuntimeError):
    """Raised when the release guard should fail the workflow."""


def run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def load_event(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    event_path = Path(path)
    if not event_path.exists():
        return {}
    return json.loads(event_path.read_text(encoding="utf-8"))


def pull_request(event: dict[str, Any]) -> dict[str, Any] | None:
    pr = event.get("pull_request")
    return pr if isinstance(pr, dict) else None


def _repo_full_name(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    repo = cast(Mapping[str, Any], value)
    full_name = repo.get("full_name")
    if isinstance(full_name, str) and full_name:
        return full_name
    owner = repo.get("owner")
    owner_repo = cast(Mapping[str, Any], owner) if isinstance(owner, Mapping) else {}
    owner_login = owner_repo.get("login")
    name = repo.get("name")
    if isinstance(owner_login, str) and owner_login and isinstance(name, str) and name:
        return f"{owner_login}/{name}"
    return ""


def canonical_repository(event: dict[str, Any], pr: dict[str, Any]) -> str:
    base = pr.get("base")
    if isinstance(base, dict):
        base_repo = _repo_full_name(base.get("repo"))
        if base_repo:
            return base_repo
    event_repo = _repo_full_name(event.get("repository"))
    if event_repo:
        return event_repo
    return os.environ.get("GITHUB_REPOSITORY", "").strip()


def require_canonical_head_repository(event: dict[str, Any], pr: dict[str, Any]) -> None:
    head = pr.get("head")
    head_repo = _repo_full_name(head.get("repo") if isinstance(head, dict) else None)
    expected_repo = canonical_repository(event, pr)
    if not expected_repo:
        raise GuardError("Could not determine the canonical repository for beta release branch ownership.")
    if not head_repo:
        raise GuardError("Could not determine the beta release PR head repository.")
    if head_repo.casefold() != expected_repo.casefold():
        raise GuardError(
            "Beta release metadata changes must come from the canonical repository release branch.\n"
            f"Expected head repository: {expected_repo}\n"
            f"Actual head repository: {head_repo}"
        )


def changed_release_files(root: Path, base_ref: str) -> list[str]:
    proc = run_git(root, "diff", "--name-only", f"{base_ref}...HEAD", "--", *RELEASE_MANAGED_FILES)
    changed = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    return sorted(set(changed))


def changed_release_version_files(root: Path, base_ref: str) -> list[str]:
    """Return release-managed files whose version value changed from *base_ref*."""

    current_versions = read_project_versions(root)
    changed: list[str] = []
    for name, current_version in current_versions.items():
        path = name.split(" ", 1)[0]
        proc = run_git(root, "show", f"{base_ref}:{path}", check=False)
        if proc.returncode != 0:
            changed.append(name)
            continue

        original = root / path
        original_text = original.read_text(encoding="utf-8")
        try:
            original.write_text(proc.stdout, encoding="utf-8")
            base_version = read_project_versions(root)[name]
        finally:
            original.write_text(original_text, encoding="utf-8")

        if base_version != current_version:
            changed.append(name)
    return changed


def _read_ref_text(root: Path, ref: str, path: str) -> str:
    proc = run_git(root, "show", f"{ref}:{path}")
    return proc.stdout


def _read_project_versions_at_ref(root: Path, ref: str) -> dict[str, str]:
    package_data = json.loads(_read_ref_text(root, ref, "frontend/package.json"))
    chart_text = _read_ref_text(root, ref, "deploy/helm/codex-lb/Chart.yaml")
    uv_text = _read_ref_text(root, ref, "uv.lock")

    def find(pattern: str, text: str, name: str) -> str:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if not match:
            raise GuardError(f"could not find {name} in {ref}")
        return match.group(1)

    return {
        "pyproject.toml": read_pyproject_version_text(_read_ref_text(root, ref, "pyproject.toml")),
        "app/__init__.py": find(
            r'^__version__ = "([^"]+)"',
            _read_ref_text(root, ref, "app/__init__.py"),
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


def _canonical_release_version(value: str) -> str:
    match = _PEP440_PRERELEASE_RE.fullmatch(value)
    if match is None:
        return value
    channel = _PEP440_CHANNELS[match.group("channel")]
    return f"{match.group('base')}-{channel}.{match.group('serial')}"


def _canonical_release_version_for_file(name: str, value: str) -> str:
    if name == "uv.lock":
        return _canonical_release_version(value)
    return value


def _canonical_release_versions(versions: Mapping[str, str]) -> dict[str, str]:
    return {name: _canonical_release_version_for_file(name, version) for name, version in versions.items()}


def read_consistent_release_version(root: Path) -> ReleaseVersion:
    versions = _canonical_release_versions(read_project_versions(root))
    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        detail = ", ".join(f"{name}={version!r}" for name, version in sorted(versions.items()))
        raise GuardError(f"Release-managed version files must agree before beta release gating: {detail}")
    return parse_version(next(iter(unique_versions)))


def _normalized_body(body: str) -> str:
    return re.sub(r"\s+", " ", body.casefold())


def require_validation_evidence(body: str, expected_sha: str) -> None:
    normalized = _normalized_body(body)
    missing: list[str] = []

    if "release-candidate validation" not in normalized:
        missing.append("a `Release-candidate validation` section")

    sha_matches = [match.group(0).lower() for match in _SHA_RE.finditer(body)]
    if expected_sha.lower() not in sha_matches:
        missing.append(f"the exact validated candidate SHA `{expected_sha}`")

    for label in _REQUIRED_EVIDENCE_LABELS:
        checked_pattern = re.compile(rf"- \[x\] [^\n]*{re.escape(label)}", re.IGNORECASE)
        if not checked_pattern.search(body):
            missing.append(f"checked evidence item `{label}`")

    if not any(
        re.search(rf"- \[x\] [^\n]*{re.escape(label)}", body, flags=re.IGNORECASE)
        for label in _LIVE_SMOKE_ACCEPTED_LABELS
    ):
        missing.append("checked evidence item for live upstream/account smoke, or an explicit not-required entry")

    if missing:
        detail = "\n".join(f"- {item}" for item in missing)
        raise GuardError(
            "Beta release PRs cannot publish until release-candidate validation evidence is recorded.\n"
            f"Missing:\n{detail}"
        )


def guard_pull_request(root: Path, event: dict[str, Any], base_ref: str, head_ref: str) -> None:
    pr = pull_request(event)
    if pr is None and os.environ.get("GITHUB_EVENT_NAME"):
        print("No pull_request payload; beta release PR guard is a no-op for this event.")
        return

    if pr is not None:
        head_ref = head_ref or str(pr.get("head", {}).get("ref") or "")
        expected_sha = str(pr.get("head", {}).get("sha") or "")
        body = str(pr.get("body") or "")
    else:
        expected_sha = run_git(root, "rev-parse", "HEAD").stdout.strip()
        body = os.environ.get("BETA_RELEASE_PR_BODY", "")

    current_versions = _canonical_release_versions(read_project_versions(root))
    release_from_head: ReleaseVersion | None = None
    if head_ref.startswith("release/beta-"):
        try:
            release_from_head = parse_version(head_ref.removeprefix("release/beta-"))
        except ValueError:
            release_from_head = None
    is_canonical_beta_pr = bool(
        release_from_head is not None
        and release_from_head.channel == "beta"
        and all(version == release_from_head.version for version in current_versions.values())
    )

    changed = changed_release_version_files(root, base_ref)
    if not changed and not is_canonical_beta_pr:
        print("No release-managed version files changed; beta release PR guard passed.")
        return

    base_versions = _canonical_release_versions(_read_project_versions_at_ref(root, base_ref))
    if current_versions == base_versions and not is_canonical_beta_pr:
        print("No release-managed version files changed; release metadata is unchanged; beta release PR guard passed.")
        return

    release = read_consistent_release_version(root)
    if release.channel != "beta":
        print(f"Release-managed files changed for non-beta version {release.version}; beta guard passed.")
        return

    canonical_branch = f"release/beta-{release.version}"
    if head_ref != canonical_branch:
        raise GuardError(
            "Beta release metadata changes must come from the canonical beta release branch.\n"
            f"Expected head branch: {canonical_branch}\n"
            f"Actual head branch: {head_ref or '<unknown>'}\n"
            f"Changed release files: {', '.join(changed)}"
        )
    if pr is not None:
        require_canonical_head_repository(event, pr)

    if not expected_sha:
        raise GuardError("Could not determine the PR head SHA for beta release validation evidence.")

    require_validation_evidence(body, expected_sha)
    print(f"Beta release PR guard passed for {canonical_branch} at {expected_sha}.")


def _commit_tree(root: Path, commit: str) -> str:
    proc = run_git(root, "rev-parse", f"{commit}^{{tree}}", check=False)
    if proc.returncode == 0:
        return proc.stdout.strip()

    # GitHub's pull_request.closed checkout is normally on the merge commit, so
    # the PR head commit may not be present locally. Fetch by object id before
    # deciding the validated candidate cannot be compared.
    fetch = run_git(root, "fetch", "origin", commit, "--depth=1", check=False)
    if fetch.returncode != 0:
        raise GuardError(
            f"Could not fetch validated candidate commit {commit} to compare release trees: {fetch.stderr.strip()}"
        )
    proc = run_git(root, "rev-parse", f"{commit}^{{tree}}", check=False)
    if proc.returncode != 0:
        raise GuardError(f"Could not resolve tree for commit {commit}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def require_published_tree_matches_validated_candidate(root: Path, *, published_sha: str, validated_sha: str) -> None:
    published_tree = _commit_tree(root, published_sha)
    validated_tree = _commit_tree(root, validated_sha)
    if published_tree != validated_tree:
        raise GuardError(
            "Refusing to publish because the merge commit tree differs from the validated candidate head.\n"
            f"Published commit: {published_sha} tree {published_tree}\n"
            f"Validated candidate: {validated_sha} tree {validated_tree}\n"
            "Update the beta PR onto the final base and rerun release-candidate validation for the new head."
        )


def guard_publish(root: Path, event: dict[str, Any]) -> None:
    pr = pull_request(event)
    if pr is None:
        raise GuardError("Publish guard requires a pull_request event payload.")

    if pr.get("merged") is not True:
        print("Pull request was not merged; beta publish guard is a no-op.")
        return

    raw_head = pr.get("head")
    head: dict[str, Any] = raw_head if isinstance(raw_head, dict) else {}
    head_ref = str(head.get("ref") or "")
    head_sha = str(head.get("sha") or "")
    body = str(pr.get("body") or "")
    published_sha = str(pr.get("merge_commit_sha") or "") or run_git(root, "rev-parse", "HEAD").stdout.strip()
    release = parse_version(read_pyproject_version(root))
    if release.channel != "beta":
        raise GuardError(f"Publish Beta Release expected beta metadata, got {release.version}.")

    canonical_branch = f"release/beta-{release.version}"
    if head_ref != canonical_branch:
        raise GuardError(
            "Refusing to publish beta release from a non-canonical branch.\n"
            f"Expected head branch: {canonical_branch}\n"
            f"Actual head branch: {head_ref or '<unknown>'}"
        )
    require_canonical_head_repository(event, pr)
    if not head_sha:
        raise GuardError("Could not determine merged beta PR head SHA.")

    require_validation_evidence(body, head_sha)
    require_published_tree_matches_validated_candidate(root, published_sha=published_sha, validated_sha=head_sha)
    print(
        f"Beta publish guard passed for {canonical_branch}; validated candidate {head_sha} "
        f"matches published commit {published_sha}."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--mode", choices=["pr", "publish"], default="pr")
    parser.add_argument("--base-ref", default="origin/main", help="base ref for PR diff checks")
    parser.add_argument("--head-ref", default=os.environ.get("GITHUB_HEAD_REF", ""), help="PR head branch name")
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH", ""), help="GitHub event JSON path")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    event = load_event(args.event_path)
    try:
        if args.mode == "pr":
            guard_pull_request(root, event, args.base_ref, args.head_ref)
        else:
            guard_publish(root, event)
    except GuardError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
