#!/usr/bin/env python3
"""Enforce simplicity budgets on README, .env.example, and the dashboard core nav.

Budgets live in .github/simplicity-budgets.toml and are enforced by
.github/workflows/simplicity-budgets.yml. Intentionally stdlib-only so it runs
on the runner's python3 before project dependencies are installed.

Override: the 'simplicity-budget-approved' PR label (passed in via the
PR_LABELS env var as a JSON array of label names) downgrades violations to
warning annotations and exits 0. push and merge_group events carry no PR
labels, so main itself must always satisfy the budgets in-file.

Exit codes: 0 = within budget (or overridden), 1 = over budget,
2 = configuration error (the budget config is missing or malformed, a
budgeted file or the nav array is missing, or an ALL-CONTRIBUTORS-LIST
block is opened but never closed).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

OVERRIDE_LABEL = "simplicity-budget-approved"
CONFIG_PATH = Path(".github/simplicity-budgets.toml")
CONTRIBUTORS_START = "<!-- ALL-CONTRIBUTORS-LIST:START"
CONTRIBUTORS_END = "<!-- ALL-CONTRIBUTORS-LIST:END"
# CommonMark code fences: ``` or ~~~ (3+ characters), indented up to 3 spaces.
FENCE_RE = re.compile(r"^ {0,3}(?P<fence>```+|~~~+)")
# CommonMark ATX headings may be indented up to 3 spaces, like fences.
HEADING_RE = re.compile(r"^ {0,3}#{1,2}\s")

CONFIG_ERROR_EXIT = 2

OVERRIDE_HELP = (
    f"To accept a temporary exceedance during review, a maintainer adds the '{OVERRIDE_LABEL}' "
    "PR label — labeling starts a fresh check run, and because the workflow fetches the live "
    "label set from the API you may also simply re-run this failed run after labeling. "
    "merge_group runs carry no PR labels, so any merge that leaves main over "
    f"budget must raise the budget in {CONFIG_PATH} in the same diff."
)


def _config_error(message: str) -> NoReturn:
    print(
        f"::error::{message} — if the file was moved or the array renamed, update "
        f"{CONFIG_PATH} in the same PR so the budget keeps applying; this check refuses "
        "to pass silently when its target disappears."
    )
    sys.exit(CONFIG_ERROR_EXIT)


def _read_lines(path: Path, section: str) -> list[str]:
    if not path.is_file():
        _config_error(f"[{section}] budgeted file '{path}' not found")
    return path.read_text(encoding="utf-8").splitlines()


def strip_contributors_block(lines: list[str]) -> list[str]:
    """Drop the generated all-contributors table (START..END marker lines inclusive)."""
    kept: list[str] = []
    in_block = False
    for line in lines:
        if not in_block and CONTRIBUTORS_START in line:
            in_block = True
            continue
        if in_block:
            if CONTRIBUTORS_END in line:
                in_block = False
            continue
        kept.append(line)
    if in_block:
        _config_error(
            f"[readme] '{CONTRIBUTORS_START}' marker has no matching '{CONTRIBUTORS_END}' marker; "
            "an unclosed block would silently exclude the rest of the file from the budget"
        )
    return kept


def count_top_level_headings(lines: list[str]) -> int:
    """Count h1/h2 headings, ignoring lines inside fenced code blocks.

    Fences may use ``` or ~~~ and be indented up to 3 spaces (CommonMark);
    a fence is closed only by a fence line using the same character, at
    least the same length, and no info string (so a ``` line inside a
    ```` block and a ```bash line inside a ``` block are both content).
    """
    fence_open: tuple[str, int] | None = None
    count = 0
    for line in lines:
        fence = FENCE_RE.match(line)
        if fence is not None:
            fence_str = fence.group("fence")
            char, length = fence_str[0], len(fence_str)
            bare = line[fence.end() :].strip() == ""
            if fence_open is None:
                fence_open = (char, length)
            elif char == fence_open[0] and length >= fence_open[1] and bare:
                fence_open = None
            continue
        if fence_open is None and HEADING_RE.match(line):
            count += 1
    return count


def count_nav_items(path: Path, array: str) -> int:
    """Count `to:` entries in the configured nav array; exit 2 loudly if it is missing."""
    if not path.is_file():
        _config_error(f"[core_nav] nav file '{path}' not found")
    # Anchor on a line-start `] as const` close: the top-level array close
    # sits at column 0, while nested arrays inside items (e.g. a future
    # `roles: ["admin"]` or `matches: ["/foo"] as const`) are indented, so
    # they cannot truncate the body early. The nav source-of-truth array is
    # required to stay `as const` with its close bracket at column 0.
    # `(?!\w)` pins the exact identifier: a rename to e.g. NAV_ITEMS_V2
    # must not satisfy a config that still says NAV_ITEMS.
    match = re.search(
        rf"const\s+{re.escape(array)}(?!\w)[^=]*=\s*\[(?P<body>.*?)^\]\s*as\s+const",
        path.read_text(encoding="utf-8"),
        re.DOTALL | re.MULTILINE,
    )
    if match is None:
        _config_error(f"[core_nav] array '{array}' not found in '{path}'")
    return len(re.findall(r"\bto:\s*[\"']", match.group("body")))


def _override_labels() -> list[str]:
    raw = os.environ.get("PR_LABELS") or "[]"
    try:
        labels = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(labels, list):
        return []
    return [label for label in labels if isinstance(label, str)]


def main() -> int:
    try:
        config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _config_error(f"budget config '{CONFIG_PATH}' not found")
    except tomllib.TOMLDecodeError as exc:
        _config_error(f"budget config '{CONFIG_PATH}' is not valid TOML: {exc}")
    overridden = OVERRIDE_LABEL in _override_labels()

    try:
        readme_cfg = config["readme"]
        readme_path = Path(readme_cfg["path"])
        readme_max_lines = int(readme_cfg["max_lines"])
        readme_max_headings = int(readme_cfg["max_top_level_headings"])
        env_cfg = config["env_example"]
        env_path = Path(env_cfg["path"])
        env_max_lines = int(env_cfg["max_lines"])
        nav_cfg = config["core_nav"]
        nav_path = Path(nav_cfg["path"])
        nav_array = str(nav_cfg["array"])
        nav_max_items = int(nav_cfg["max_items"])
    except (KeyError, TypeError, ValueError) as exc:
        _config_error(f"budget config '{CONFIG_PATH}' is missing or has a malformed section/key: {exc!r}")

    readme_lines = strip_contributors_block(_read_lines(readme_path, "readme"))
    env_lines = _read_lines(env_path, "env_example")
    nav_items = count_nav_items(nav_path, nav_array)

    metrics: list[tuple[str, Path, int, int]] = [
        (
            "README lines (all-contributors block excluded)",
            readme_path,
            len(readme_lines),
            readme_max_lines,
        ),
        (
            "README top-level headings (h1+h2, fenced code excluded)",
            readme_path,
            count_top_level_headings(readme_lines),
            readme_max_headings,
        ),
        ("env example lines", env_path, len(env_lines), env_max_lines),
        (f"core nav items ({nav_array})", nav_path, nav_items, nav_max_items),
    ]

    violations: list[tuple[str, Path, int, int]] = []
    for name, path, actual, budget in metrics:
        status = "OK" if actual <= budget else "OVER"
        print(f"{name}: {actual}/{budget} {status}")
        if actual > budget:
            violations.append((name, path, actual, budget))

    if not violations:
        return 0

    annotation = "warning" if overridden else "error"
    for name, path, actual, budget in violations:
        print(f"::{annotation} file={path}::simplicity budget exceeded: {name}: {actual} > {budget}")

    if overridden:
        print(f"Budgets exceeded, but the '{OVERRIDE_LABEL}' label is applied; passing with warnings. {OVERRIDE_HELP}")
        return 0

    print(f"Simplicity budgets exceeded. {OVERRIDE_HELP}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
