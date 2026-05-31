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
import urllib.request
from pathlib import Path

NOREPLY_RE = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$")


def _request_json(url: str, token: str | None) -> tuple[list[dict[str, object]], str | None]:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
        link = response.headers.get("Link")
    return payload, link


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        bits = part.strip().split(";")
        if len(bits) != 2:
            continue
        url_part, rel_part = bits
        if rel_part.strip() == 'rel="next"':
            return url_part.strip()[1:-1]
    return None


def fetch_contributor_logins(repository: str, token: str | None) -> set[str]:
    url: str | None = f"https://api.github.com/repos/{repository}/contributors?per_page=100&anon=false"
    logins: set[str] = set()
    while url:
        try:
            page, link = _request_json(url, token)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SystemExit(f"GitHub contributors request failed: HTTP {exc.code}: {detail}") from exc
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


def pull_request_author_login(event_path: str | None) -> set[str]:
    if not event_path:
        return set()
    path = Path(event_path)
    if not path.exists():
        return set()
    event = json.loads(path.read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        return set()
    user = pull_request.get("user")
    if not isinstance(user, dict):
        return set()
    login = user.get("login")
    user_type = user.get("type")
    if not isinstance(login, str) or user_type == "Bot" or login.endswith("[bot]"):
        return set()
    return {login.lower()}


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
