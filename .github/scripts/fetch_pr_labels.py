#!/usr/bin/env python3
"""Print the current PR labels as compact JSON, retrying transient API failures."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    from github_api import GitHubApiError, next_link, request_json
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from github_api import GitHubApiError, next_link, request_json


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY")
    pr_number = os.environ.get("PR_NUMBER")
    if not repository or not pr_number:
        raise SystemExit("GITHUB_REPOSITORY and PR_NUMBER are required")
    url: str | None = f"https://api.github.com/repos/{repository}/issues/{pr_number}/labels?per_page=100"
    labels: list[str] = []
    while url:
        try:
            payload, link = request_json(url, token=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
        except GitHubApiError as exc:
            print(
                f"warning: GitHub PR labels request failed after retries; continuing without override labels: {exc}",
                file=sys.stderr,
                flush=True,
            )
            labels.clear()
            break
        if not isinstance(payload, list):
            raise SystemExit(f"GitHub PR labels request returned {type(payload).__name__}, expected list")
        labels.extend(item["name"] for item in payload if isinstance(item, dict) and isinstance(item.get("name"), str))
        url = next_link(link)
    print(json.dumps(labels, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
