#!/usr/bin/env python3
"""Close beta release PRs superseded by the current release train.

The Sync Beta Release PR workflow creates release PRs from branches named
``release/beta-X.Y.Z-beta.N``. When release-please retargets the stable train,
older automation-created beta PRs can remain open. This script intentionally
cleans up only PRs carrying the automation sentinel in their body so manually
created beta branches are not touched by accident.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

_AUTOMATION_SENTINEL_RE = re.compile(r"Synced automatically from release-please PR #\d+")
_BETA_RELEASE_TITLE_RE = re.compile(r"^chore: release v\d+\.\d+\.\d+-beta\.\d+$")
_PROTECTED_LABELS = frozenset({"pinned", "security", "in-progress"})


@dataclass(frozen=True)
class CleanupAction:
    pr_number: int
    head_ref: str
    comment: str
    delete_ref_api_path: str


def _label_names(labels: object) -> set[str]:
    names: set[str] = set()
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, dict):
            name = cast("dict[str, object]", label).get("name")
            if isinstance(name, str):
                names.add(name)
    return names


def _head_owner(pr: Mapping[str, Any]) -> str:
    owner = pr.get("headRepositoryOwner")
    if isinstance(owner, Mapping) and isinstance(owner.get("login"), str):
        return owner["login"]
    return ""


def is_cleanup_candidate(pr: Mapping[str, Any], *, current_branch: str, repo_owner: str) -> bool:
    """Return whether an open PR is an automation-managed superseded beta PR."""

    head_ref = pr.get("headRefName")
    if not isinstance(head_ref, str):
        return False
    if head_ref == current_branch or not head_ref.startswith("release/beta-"):
        return False

    if _head_owner(pr) != repo_owner:
        return False

    title = pr.get("title")
    if not isinstance(title, str) or not _BETA_RELEASE_TITLE_RE.fullmatch(title):
        return False

    body = pr.get("body")
    if not isinstance(body, str) or not _AUTOMATION_SENTINEL_RE.search(body):
        return False

    if _label_names(pr.get("labels")) & _PROTECTED_LABELS:
        return False

    return True


def cleanup_plan(
    prs: Iterable[Mapping[str, Any]],
    *,
    current_branch: str,
    current_tag: str,
    repo: str,
    repo_owner: str,
    release_pr: str,
) -> list[CleanupAction]:
    """Build the actions needed to close superseded beta release PRs."""

    actions: list[CleanupAction] = []
    for pr in prs:
        if not is_cleanup_candidate(pr, current_branch=current_branch, repo_owner=repo_owner):
            continue
        pr_number = pr.get("number")
        head_ref = pr.get("headRefName")
        if not isinstance(pr_number, int) or not isinstance(head_ref, str):
            continue
        comment = (
            f"Superseded by the current beta release train `{current_tag}` "
            f"from release-please PR #{release_pr}. Closing this automation-managed "
            "beta PR to avoid publishing an obsolete train."
        )
        actions.append(
            CleanupAction(
                pr_number=pr_number,
                head_ref=head_ref,
                comment=comment,
                delete_ref_api_path=f"repos/{repo}/git/refs/heads/{head_ref}",
            )
        )
    return actions


def _run_gh(args: Sequence[str]) -> str:
    gh = shutil.which("gh") or "gh"
    proc = subprocess.run(
        [gh, *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return proc.stdout


def fetch_open_prs(repo: str) -> list[Mapping[str, Any]]:
    output = _run_gh(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--base",
            "main",
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,headRefName,headRepositoryOwner,labels,body",
        ]
    )
    parsed = json.loads(output or "[]")
    if not isinstance(parsed, list):
        raise ValueError("gh pr list did not return a JSON array")
    return [item for item in parsed if isinstance(item, Mapping)]


def execute_cleanup(actions: Iterable[CleanupAction], *, repo: str, dry_run: bool) -> None:
    for action in actions:
        if dry_run:
            print(f"Would close PR #{action.pr_number} ({action.head_ref}) and delete branch")
            continue
        _run_gh(["pr", "comment", str(action.pr_number), "--repo", repo, "--body", action.comment])
        _run_gh(["pr", "close", str(action.pr_number), "--repo", repo])
        _run_gh(["api", "-X", "DELETE", action.delete_ref_api_path])
        print(f"Closed superseded beta PR #{action.pr_number} and deleted {action.head_ref}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--repo-owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""))
    parser.add_argument("--current-branch", required=True)
    parser.add_argument("--current-tag", required=True)
    parser.add_argument("--release-pr", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.repo:
        raise SystemExit("--repo is required when GITHUB_REPOSITORY is not set")
    repo_owner = args.repo_owner or args.repo.split("/", 1)[0]

    prs = fetch_open_prs(args.repo)
    actions = cleanup_plan(
        prs,
        current_branch=args.current_branch,
        current_tag=args.current_tag,
        repo=args.repo,
        repo_owner=repo_owner,
        release_pr=args.release_pr,
    )
    if not actions:
        print("No superseded beta release PRs to close.")
        return 0
    execute_cleanup(actions, repo=args.repo, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
