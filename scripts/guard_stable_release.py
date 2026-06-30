#!/usr/bin/env python3
"""Guard stable release PRs against partial release-managed version promotion.

This script is intentionally stdlib-only so it can run early in GitHub Actions,
before project dependencies are installed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from scripts.guard_beta_release import (
    GuardError,
    canonical_repository,
    load_event,
    pull_request,
    require_canonical_head_repository,
)
from scripts.release_versions import (
    canonical_release_version_for_file,
    parse_version,
    read_project_versions,
)

RELEASE_PLEASE_BRANCH = "release-please--branches--main"


def _canonical_versions(versions: Mapping[str, str]) -> dict[str, str]:
    return {name: canonical_release_version_for_file(name, version) for name, version in versions.items()}


def _read_project_versions_at_ref(root: Path, ref: str) -> dict[str, str]:
    # Reuse the beta guard's ref reader indirectly by checking out file content is
    # deliberately avoided here; instead invoke the existing public helper by
    # running it against a temporary index-less materialization would be overkill.
    # The stable guard only needs a small fixed set of version fields, so keep this
    # logic local and stdlib-only.
    import json
    import re
    import subprocess

    def git_show(path: str) -> str:
        return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=root, text=True)

    def find(pattern: str, text: str, name: str) -> str:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if not match:
            raise GuardError(f"could not find {name} in {ref}")
        return match.group(1)

    pyproject_text = git_show("pyproject.toml")
    try:
        import tomllib

        pyproject_data = tomllib.loads(pyproject_text)
        project = pyproject_data.get("project")
        pyproject_version = project.get("version") if isinstance(project, dict) else None
    except Exception as exc:  # pragma: no cover - defensive corruption path
        raise GuardError(f"could not read pyproject version at {ref}: {exc}") from exc
    if not isinstance(pyproject_version, str) or not pyproject_version:
        raise GuardError(f"could not find pyproject version in {ref}")

    package_data = json.loads(git_show("frontend/package.json"))
    chart_text = git_show("deploy/helm/codex-lb/Chart.yaml")
    uv_text = git_show("uv.lock")
    return {
        "pyproject.toml": pyproject_version,
        "app/__init__.py": find(r'^__version__ = "([^"]+)"', git_show("app/__init__.py"), "app version"),
        "frontend/package.json": package_data["version"],
        "deploy/helm/codex-lb/Chart.yaml version": find(r"^version: (.+)$", chart_text, "chart version"),
        "deploy/helm/codex-lb/Chart.yaml appVersion": find(r"^appVersion: (.+)$", chart_text, "chart appVersion"),
        "uv.lock": find(
            r'\[\[package\]\]\nname = "codex-lb"\nversion = "([^"]+)"\nsource = \{ editable = "\." \}',
            uv_text,
            "uv.lock codex-lb version",
        ),
    }


def _changed_version_fields(base_versions: Mapping[str, str], current_versions: Mapping[str, str]) -> list[str]:
    base = _canonical_versions(base_versions)
    current = _canonical_versions(current_versions)
    return sorted(name for name, current_version in current.items() if base.get(name) != current_version)


def _require_consistent_current_versions(current_versions: Mapping[str, str]) -> str:
    canonical = _canonical_versions(current_versions)
    unique_versions = set(canonical.values())
    if len(unique_versions) != 1:
        detail = ", ".join(f"{name}={version!r}" for name, version in sorted(canonical.items()))
        raise GuardError(f"Stable release-managed version files must agree before promotion: {detail}")
    return next(iter(unique_versions))


def guard_stable_pull_request(root: Path, event_path: str, base_ref: str, head_ref: str) -> None:
    event = load_event(event_path)
    pr = pull_request(event)
    if pr is not None:
        head_ref = head_ref or str(pr.get("head", {}).get("ref") or "")

    current_versions = read_project_versions(root)
    base_versions = _read_project_versions_at_ref(root, base_ref)
    changed = _changed_version_fields(base_versions, current_versions)
    if "pyproject.toml" not in changed:
        print("No stable release promotion detected; stable release guard passed.")
        return

    release = parse_version(canonical_release_version_for_file("pyproject.toml", current_versions["pyproject.toml"]))
    if release.channel != "stable":
        print(f"Release promotion target {release.version} is not stable; stable release guard passed.")
        return

    expected_changed = sorted(current_versions)
    if head_ref:
        if head_ref != RELEASE_PLEASE_BRANCH:
            raise GuardError(
                "Stable release promotions must come from the release-please branch.\n"
                f"Expected head branch: {RELEASE_PLEASE_BRANCH}\n"
                f"Actual head branch: {head_ref}\n"
                f"Changed release fields: {', '.join(changed)}"
            )
    elif pr is not None:
        raise GuardError("Could not determine the stable release PR head branch.")
    else:
        print("No PR head branch available; relying on the pull_request stable release guard for branch ownership.")

    if pr is not None:
        require_canonical_head_repository(event, pr)

    current_version = _require_consistent_current_versions(current_versions)
    if changed != expected_changed:
        missing = sorted(set(expected_changed) - set(changed))
        raise GuardError(
            "Stable release promotions must advance every release-managed version field together.\n"
            f"Version: {current_version}\n"
            f"Changed fields: {', '.join(changed)}\n"
            f"Missing changed fields: {', '.join(missing)}"
        )

    source = canonical_repository(event, pr or {}) or head_ref
    print(f"Stable release guard passed for {current_version} from {source}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--base-ref", default="origin/main", help="base ref for PR diff checks")
    parser.add_argument("--head-ref", default=os.environ.get("GITHUB_HEAD_REF", ""), help="PR head branch name")
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH", ""), help="GitHub event JSON path")
    args = parser.parse_args()

    try:
        guard_stable_pull_request(Path(args.root).resolve(), args.event_path, args.base_ref, args.head_ref)
    except GuardError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
