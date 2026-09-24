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

NOREPLY_RE = re.compile(r"^(?:(?P<id>\d+)\+)?(?P<login>[^@]+)@users\.noreply\.github\.com$")


def _request_json(url: str, token: str | None) -> tuple[list[dict[str, object]], str | None]:
    """Read one complete GitHub list page using the shared retry policy."""
    payload, link = request_json(url, token=token)
    if not isinstance(payload, list):
        raise GitHubApiError(f"expected list payload, got {type(payload).__name__}")
    return payload, link


def _next_link(link_header: str | None) -> str | None:
    """Find the next page without treating partial coverage as complete."""
    return next_link(link_header)


def _human_identity(user: dict[str, object]) -> tuple[str, int | None] | None:
    """Retain human logins and only genuine positive integer GitHub IDs."""
    login = user.get("login")
    if not isinstance(login, str) or user.get("type") == "Bot" or login.endswith("[bot]"):
        return None
    user_id = user.get("id")
    # bool is an int subclass, but it is not a GitHub user ID.
    valid_id = user_id if type(user_id) is int and user_id > 0 else None
    return login.lower(), valid_id


def fetch_contributors(repository: str, token: str | None) -> dict[int | str, str]:
    """Fetch human logins keyed by stable ID, or by login when the ID is unavailable."""
    url: str | None = f"https://api.github.com/repos/{repository}/contributors?per_page=100&anon=false"
    contributors: dict[int | str, str] = {}
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
            identity = _human_identity(contributor)
            if identity is None:
                continue
            login, user_id = identity
            contributors[user_id if user_id is not None else login] = login
        url = _next_link(link)
    return contributors


def local_commit_author_logins(logins_by_id: dict[int, str] | None = None) -> set[str]:
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
            login = match.group("login").lower()
            if not login.endswith("[bot]"):
                user_id = match.group("id")
                if logins_by_id is not None and user_id is not None:
                    login = logins_by_id.get(int(user_id), login)
                logins.add(login)
    return logins


def _pull_request_event(event_path: str | None) -> dict[str, object] | None:
    """Read PR metadata when the invocation has a pull-request event."""
    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    event = json.loads(path.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    return pull_request if isinstance(pull_request, dict) else None


def pull_request_author_login(event_path: str | None, *, logins_by_id: dict[int, str] | None = None) -> set[str]:
    """Resolve a possibly stale PR opener against live API identity evidence."""
    pull_request = _pull_request_event(event_path)
    if pull_request is None:
        return set()
    user = pull_request.get("user")
    if not isinstance(user, dict):
        return set()
    user = cast(dict[str, object], user)
    identity = _human_identity(user)
    if identity is None:
        return set()
    login, user_id = identity
    if logins_by_id is not None and user_id is not None:
        # Reruns retain the original event, so prefer fresh API identity evidence.
        login = logins_by_id.setdefault(user_id, login)
    return {login}


def pull_request_commit_authors(event_path: str | None, token: str | None) -> dict[int | str, str]:
    """Fetch all human PR commit identities, refusing incomplete API evidence."""
    pull_request = _pull_request_event(event_path)
    if pull_request is None:
        return {}
    commit_count = pull_request.get("commits")
    if isinstance(commit_count, int) and commit_count > 250:
        raise SystemExit(
            "Pull request has more than 250 commits; GitHub's PR commits endpoint is capped, "
            "so all-contributors coverage cannot be validated safely."
        )
    commits_url = pull_request.get("commits_url")
    if not isinstance(commits_url, str) or not commits_url:
        return {}
    url: str | None = f"{commits_url}?per_page=100"
    authors: dict[int | str, str] = {}
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
            identity = _human_identity(author)
            if identity is None:
                continue
            login, user_id = identity
            authors[user_id if user_id is not None else login] = login
        url = _next_link(link)
    return authors


def load_all_contributors(path: Path) -> set[str]:
    """Load recorded contributor logins for case-insensitive coverage checks."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        contributor["login"].lower()
        for contributor in data.get("contributors", [])
        if isinstance(contributor, dict) and isinstance(contributor.get("login"), str)
    }


def main() -> int:
    """Check recorded coverage after reconciling API and historical identities."""
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

    token = os.environ.get("GITHUB_TOKEN")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    contributors = fetch_contributors(args.repo, token)
    # Keep IDs until both API sources are combined; repository contributor
    # results can retain an older login than the PR commit-author response.
    contributors.update(pull_request_commit_authors(event_path, token))
    logins_by_id = {user_id: login for user_id, login in contributors.items() if isinstance(user_id, int)}
    expected = set(contributors.values())
    expected |= pull_request_author_login(event_path, logins_by_id=logins_by_id)
    expected |= local_commit_author_logins(logins_by_id)
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
