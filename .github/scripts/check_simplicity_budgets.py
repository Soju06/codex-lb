#!/usr/bin/env python3
"""Enforce simplicity budgets on README, .env.example, the dashboard core nav, and the tracked root tree.

Budgets live in .github/simplicity-budgets.toml and are enforced by
.github/workflows/simplicity-budgets.yml. Intentionally stdlib-only so it runs
on the runner's python3 before project dependencies are installed; the
[root_files] check additionally shells out to `git ls-tree` against the
checkout's HEAD.

Override: the 'simplicity-budget-approved' PR label (passed in via the
PR_LABELS env var as a JSON array of label names) downgrades violations to
warning annotations and exits 0. push and merge_group events carry no PR
labels, so main itself must always satisfy the budgets in-file.

Exit codes: 0 = within budget (or overridden), 1 = over budget,
2 = configuration error (the budget config is missing or malformed, a
budgeted file or the nav array is missing, the tracked root tree cannot
be listed, or an ALL-CONTRIBUTORS-LIST block is opened but never closed).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
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


def _escape_annotation_value(value: str) -> str:
    """Escape a contributor-controlled value for a workflow-command line ('%' first, per Actions rules)."""
    for char, escape in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A"), (":", "%3A"), (",", "%2C")):
        value = value.replace(char, escape)
    return value


def list_tracked_root_entries() -> list[str]:
    """List tracked repository-root entries from HEAD; exit 2 loudly if git cannot."""
    try:
        proc = subprocess.run(
            ["git", "ls-tree", "--name-only", "-z", "HEAD"],
            capture_output=True,
            encoding="utf-8",
            # Non-UTF-8 filename bytes become \x escapes: they can never match
            # an allowlist entry, so they surface as a named violation instead
            # of a decode crash.
            errors="backslashreplace",
            check=True,
        )
    except FileNotFoundError:
        _config_error("[root_files] git executable not found; the root-entry budget needs a git checkout")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"exit code {exc.returncode}"
        _config_error(f"[root_files] 'git ls-tree --name-only HEAD' failed: {detail}")
    return [entry for entry in proc.stdout.split("\0") if entry]


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

    # [root_files] is optional: absent means the root-entry budget is not
    # enforced (older configs keep working), present-but-malformed is a
    # config error like any other section.
    root_allowed: set[str] | None = None
    root_cfg = config.get("root_files")
    if root_cfg is not None:
        try:
            allowed_entries = root_cfg["allowed"]
        except (KeyError, TypeError) as exc:
            _config_error(f"budget config '{CONFIG_PATH}' has a malformed [root_files] section: {exc!r}")
        if not isinstance(allowed_entries, list) or not all(isinstance(entry, str) for entry in allowed_entries):
            _config_error(f"budget config '{CONFIG_PATH}' [root_files] 'allowed' must be an array of strings")
        root_allowed = set(allowed_entries)

    readme_lines = strip_contributors_block(_read_lines(readme_path, "readme"))
    env_lines = _read_lines(env_path, "env_example")
    nav_items = count_nav_items(nav_path, nav_array)
    unexpected_root_entries: list[str] = []
    if root_allowed is not None:
        unexpected_root_entries = sorted(set(list_tracked_root_entries()) - root_allowed)

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

    if root_allowed is not None:
        status = "OK" if not unexpected_root_entries else "OVER"
        print(f"tracked root entries outside allowlist: {len(unexpected_root_entries)}/0 {status}")

    if not violations and not unexpected_root_entries:
        return 0

    annotation = "warning" if overridden else "error"
    for name, path, actual, budget in violations:
        print(f"::{annotation} file={path}::simplicity budget exceeded: {name}: {actual} > {budget}")
    for entry in unexpected_root_entries:
        # Entry names come from the tree, not the trusted config: escape them
        # so a crafted filename cannot break or forge workflow-command lines.
        shown = _escape_annotation_value(entry)
        print(
            f"::{annotation} file={shown}::simplicity budget exceeded: tracked root entry '{shown}' is not in "
            f"the [root_files] allowlist — add it to {CONFIG_PATH} in the same diff, or a maintainer applies "
            f"the '{OVERRIDE_LABEL}' PR label"
        )

    if overridden:
        print(f"Budgets exceeded, but the '{OVERRIDE_LABEL}' label is applied; passing with warnings. {OVERRIDE_HELP}")
        return 0

    print(f"Simplicity budgets exceeded. {OVERRIDE_HELP}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
