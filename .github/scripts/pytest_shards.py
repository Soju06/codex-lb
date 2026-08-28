"""Deterministic pytest shard selection for the integration-core CI slice.

The integration-core slice is ``tests/integration`` minus the files owned by
the integration-bridge slice. To keep CI wallclock down the slice is split
into ``--shard-count`` shards: every file's weight is its number of test
functions (a cheap static proxy for runtime) and files are greedily assigned
to the lightest shard. Assignment is a pure function of the file tree, so
every test file maps to exactly one shard by construction and files added
later are picked up automatically.

Usage:
    python .github/scripts/pytest_shards.py --shard-count 3 --shard 1
        Print the file arguments for shard 1 (one per line).
    python .github/scripts/pytest_shards.py --shard-count 3 --verify
        Assert the shards form a complete, non-overlapping partition of the
        integration-core selection and that the bridge exclusions still exist.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INTEGRATION_ROOT = Path("tests/integration")

# Files owned by the integration-bridge slice (see `make test-integration-bridge`).
# They are excluded here and must keep existing so neither slice silently loses them.
BRIDGE_FILES = (
    INTEGRATION_ROOT / "test_http_responses_bridge.py",
    INTEGRATION_ROOT / "test_proxy_websocket_responses.py",
)

# Default pytest collection globs (`python_files`); pyproject.toml does not override them.
TEST_FILE_GLOBS = ("test_*.py", "*_test.py")

TEST_FUNCTION_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)


def integration_core_files() -> list[Path]:
    root = REPO_ROOT / INTEGRATION_ROOT
    files: set[Path] = set()
    for pattern in TEST_FILE_GLOBS:
        files.update(path.relative_to(REPO_ROOT) for path in root.rglob(pattern))
    return sorted(files - set(BRIDGE_FILES))


def file_weight(path: Path) -> int:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    # Weight 1 minimum so empty/new files still get a deterministic shard.
    return max(1, len(TEST_FUNCTION_RE.findall(text)))


def shard_files(files: list[Path], shard: int, shard_count: int) -> list[Path]:
    # Greedy bin packing: heaviest file first onto the lightest shard.
    # Deterministic tie-breaks (file name, shard index) keep assignment stable.
    loads = [0] * shard_count
    shards: list[list[Path]] = [[] for _ in range(shard_count)]
    for path in sorted(files, key=lambda p: (-file_weight(p), p)):
        target = min(range(shard_count), key=lambda i: (loads[i], i))
        loads[target] += file_weight(path)
        shards[target].append(path)
    return sorted(shards[shard - 1])


def verify(files: list[Path], shard_count: int) -> None:
    for bridge_file in BRIDGE_FILES:
        if not (REPO_ROOT / bridge_file).is_file():
            raise SystemExit(
                f"bridge exclusion {bridge_file} does not exist; "
                "update BRIDGE_FILES and the Makefile integration slices together"
            )
    if not files:
        raise SystemExit("integration-core selection is empty")

    seen: dict[Path, int] = {}
    for shard in range(1, shard_count + 1):
        selected = shard_files(files, shard, shard_count)
        if not selected:
            raise SystemExit(f"shard {shard}/{shard_count} selects no files")
        for path in selected:
            if path in seen:
                raise SystemExit(f"{path} assigned to both shard {seen[path]} and shard {shard}")
            seen[path] = shard

    missing = sorted(set(files) - set(seen))
    if missing:
        raise SystemExit("files not assigned to any shard: " + ", ".join(map(str, missing)))
    print(f"OK: {len(files)} integration-core test files partitioned across {shard_count} shards")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-count", type=int, required=True, help="total number of shards")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--shard", type=int, help="1-based shard index to print files for")
    group.add_argument("--verify", action="store_true", help="assert shards partition the full selection")
    args = parser.parse_args()

    if args.shard_count < 1:
        parser.error("--shard-count must be >= 1")
    if args.shard is not None and not 1 <= args.shard <= args.shard_count:
        parser.error("--shard must be between 1 and --shard-count")

    files = integration_core_files()
    if args.verify:
        verify(files, args.shard_count)
        return
    sys.stdout.write("\n".join(str(path) for path in shard_files(files, args.shard, args.shard_count)) + "\n")


if __name__ == "__main__":
    main()
