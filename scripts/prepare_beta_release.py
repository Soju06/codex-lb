#!/usr/bin/env python3
"""Prepare a beta release version bump in the working tree."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.release_versions import (
    assert_project_versions,
    discover_release_please_base_version,
    latest_beta_tag,
    next_beta_number,
    parse_version,
    tag_targets_head,
    update_project_versions,
    write_github_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--base-version",
        default="",
        help="stable version to beta-test (defaults to release-please PR branch version)",
    )
    parser.add_argument(
        "--beta-number",
        type=int,
        default=0,
        help="beta serial number (defaults to highest existing beta tag + 1)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    base_version = args.base_version.strip() or discover_release_please_base_version(root)
    base = parse_version(base_version)
    if base.is_prerelease:
        raise SystemExit(f"--base-version must be stable, got {base.version!r}")

    latest_beta = latest_beta_tag(root, base.version)
    if args.beta_number == 0 and latest_beta is not None and tag_targets_head(root, latest_beta.tag):
        assert_project_versions(root, latest_beta.version)
        outputs = {
            "should_create": False,
            "reason": f"{latest_beta.tag} already covers HEAD",
            "base_version": base.version,
            "beta_number": latest_beta.serial or 0,
            "version": latest_beta.version,
            "tag": latest_beta.tag,
            "pypi_version": latest_beta.pypi_version,
            "branch": f"release/beta-{latest_beta.version}",
        }
        write_github_outputs(outputs)
        for key, value in outputs.items():
            print(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
        return 0

    beta_number = args.beta_number or next_beta_number(root, base.version)
    if beta_number < 1:
        raise SystemExit("--beta-number must be >= 1")

    version = f"{base.version}-beta.{beta_number}"
    release = parse_version(version)
    update_project_versions(root, release.version)

    outputs = {
        "base_version": base.version,
        "beta_number": beta_number,
        "version": release.version,
        "tag": release.tag,
        "pypi_version": release.pypi_version,
        "branch": f"release/beta-{release.version}",
    }
    write_github_outputs(outputs)
    for key, value in outputs.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
