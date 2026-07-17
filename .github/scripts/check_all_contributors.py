#!/usr/bin/env python3
"""Validate that GitHub commit contributors are listed in all-contributors."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import cast

try:
    from github_api import GitHubApiError, next_link, request_json
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from github_api import GitHubApiError, next_link, request_json

NOREPLY_RE = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$")


def _request_json(url: str, token: str | None) -> tuple[list[dict[str, object]], str | None]:
    payload, link = request_json(url, token=token)
    if not isinstance(payload, list):
        raise GitHubApiError(f"expected list payload, got {type(payload).__name__}")
    return payload, link


def _next_link(link_header: str | None) -> str | None:
    return next_link(link_header)


def fetch_contributor_logins(repository: str, token: str | None) -> set[str]:
    url: str | None = f"https://api.github.com/repos/{repository}/contributors?per_page=100&anon=false"
    logins: set[str] = set()
    while url:
        try:
            page, link = _request_json(url, token)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"GitHub contributors request failed: HTTP {exc.code}: {detail}") from exc
        except GitHubApiError as exc:
            raise SystemExit(
                "GitHub contributors request failed after retries; "
                f"cannot validate all-contributors coverage from partial evidence: {exc}"
            ) from exc
        for contributor in page:
            login = contributor.get("login")
            contributor_type = contributor.get("type")
            if not isinstance(login, str):
                continue
            if contributor_type == "Bot" or login.endswith("[bot]"):
                continue
            logins.add(login.lower())
        url = _next_link(link)
    return logins


def local_commit_author_logins() -> set[str]:
    """Best-effort local check for PR commits that are not in GitHub contributors yet."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%ae", "--no-merges"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"git log failed: {exc.stderr}") from exc

    logins: set[str] = set()
    for email in result.stdout.splitlines():
        match = NOREPLY_RE.match(email.strip())
        if match:
            login = match.group(1)
            if not login.endswith("[bot]"):
                logins.add(login.lower())
    return logins


def _pull_request_event(event_path: str | None) -> dict[str, object] | None:
    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    event = json.loads(path.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    return pull_request if isinstance(pull_request, dict) else None


def pull_request_author_login(event_path: str | None) -> set[str]:
    pull_request = _pull_request_event(event_path)
    if pull_request is None:
        return set()
    user = pull_request.get("user")
    if not isinstance(user, dict):
        return set()
    user = cast(dict[str, object], user)
    login = user.get("login")
    user_type = user.get("type")
    if not isinstance(login, str) or user_type == "Bot" or login.endswith("[bot]"):
        return set()
    return {login.lower()}


def pull_request_commit_author_logins(event_path: str | None, token: str | None) -> set[str]:
    pull_request = _pull_request_event(event_path)
    if pull_request is None:
        return set()
    commit_count = pull_request.get("commits")
    if isinstance(commit_count, int) and commit_count > 250:
        raise SystemExit(
            "Pull request has more than 250 commits; GitHub's PR commits endpoint is capped, "
            "so all-contributors coverage cannot be validated safely."
        )
    commits_url = pull_request.get("commits_url")
    if not isinstance(commits_url, str) or not commits_url:
        return set()
    url: str | None = f"{commits_url}?per_page=100"
    logins: set[str] = set()
    while url:
        try:
            page, link = _request_json(url, token)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"GitHub PR commits request failed: HTTP {exc.code}: {detail}") from exc
        except GitHubApiError as exc:
            raise SystemExit(
                "GitHub PR commits request failed after retries; "
                f"cannot validate all-contributors coverage from partial evidence: {exc}"
            ) from exc
        for commit in page:
            author = commit.get("author")
            if not isinstance(author, dict):
                continue
            author = cast(dict[str, object], author)
            login = author.get("login")
            author_type = author.get("type")
            if not isinstance(login, str) or author_type == "Bot" or login.endswith("[bot]"):
                continue
            logins.add(login.lower())
        url = _next_link(link)
    return logins


def load_all_contributors(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        contributor["login"].lower()
        for contributor in data.get("contributors", [])
        if isinstance(contributor, dict) and isinstance(contributor.get("login"), str)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "Soju06/codex-lb"),
        help="GitHub repository to check, in owner/name form",
    )
    parser.add_argument(
        "--config",
        default=".all-contributorsrc",
        type=Path,
        help="Path to the all-contributors config",
    )
    args = parser.parse_args()

    expected = (
        fetch_contributor_logins(args.repo, os.environ.get("GITHUB_TOKEN"))
        | local_commit_author_logins()
        | pull_request_author_login(os.environ.get("GITHUB_EVENT_PATH"))
        | pull_request_commit_author_logins(os.environ.get("GITHUB_EVENT_PATH"), os.environ.get("GITHUB_TOKEN"))
    )
    recorded = load_all_contributors(args.config)
    missing = sorted(expected - recorded)

    if not missing:
        print(f"all-contributors covers {len(expected)} GitHub commit contributors")
        return 0

    print("Missing GitHub commit contributors in .all-contributorsrc:", file=sys.stderr)
    for login in missing:
        print(f"  - {login}", file=sys.stderr)
    print(
        "\nAdd the missing people with all-contributors before merging, or update this checker if GitHub",
        "contributors semantics change.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
