#!/usr/bin/env python3
"""Verify that release-managed files agree with the release tag/version."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.release_versions import (
    assert_project_versions,
    parse_tag,
    parse_version,
    read_pyproject_version,
    write_github_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--tag", default="", help="release tag to compare with project files")
    parser.add_argument("--require-channel", choices=["stable", "alpha", "beta", "rc"], default="")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    release = parse_tag(args.tag) if args.tag else parse_version(read_pyproject_version(root))
    if args.require_channel and release.channel != args.require_channel:
        raise SystemExit(f"expected {args.require_channel} release, got {release.channel}: {release.version}")

    assert_project_versions(root, release.version)
    outputs = {
        "tag": release.tag,
        "version": release.version,
        "base_version": release.base,
        "channel": release.channel,
        "is_prerelease": release.is_prerelease,
        "pypi_version": release.pypi_version,
    }
    write_github_outputs(outputs)
    for key, value in outputs.items():
        print(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
