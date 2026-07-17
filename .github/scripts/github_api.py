"""Small GitHub API helpers for CI scripts.

GitHub occasionally returns transient 5xx responses or an HTML "Unicorn" page
to otherwise normal API calls. CI preflight jobs should sleep and retry those
failures instead of turning a healthy PR red in a few seconds.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_ATTEMPTS = 5
DEFAULT_BASE_DELAY_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 30.0


class GitHubApiError(RuntimeError):
    """Raised when a GitHub API request fails after retries."""


def _retry_delay(attempt_index: int, base_delay_seconds: float) -> float:
    return min(base_delay_seconds * (2**attempt_index), 30.0)


def request_json(
    url: str,
    *,
    token: str | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Any, str | None]:
    """Fetch a GitHub JSON payload, retrying transient transport/API failures."""

    token = token if token is not None else os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    last_error: str | None = None
    for attempt_index in range(attempts):
        request = urllib.request.Request(url)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if token:
            request.add_header("Authorization", f"Bearer {token}")

        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw), response.headers.get("Link")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {exc.code}: {detail[:500]}"
            if exc.code not in RETRY_STATUS_CODES or attempt_index + 1 >= attempts:
                raise GitHubApiError(last_error) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt_index + 1 >= attempts:
                raise GitHubApiError(last_error) from exc

        delay = _retry_delay(attempt_index, base_delay_seconds)
        print(
            f"warning: GitHub API request failed ({last_error}); retrying in {delay:g}s",
            file=sys.stderr,
            flush=True,
        )
        sleep(delay)

    raise GitHubApiError(last_error or "GitHub API request failed")


def next_link(link_header: str | None) -> str | None:
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
