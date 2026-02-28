from __future__ import annotations

import argparse
import re
from pathlib import Path

from app.db.alembic.revision_ids import OLD_TO_NEW_REVISION_MAP

_REVISION_ASSIGNMENT_PATTERN = re.compile(r'^(revision\s*=\s*")([^"]+)(")$', re.MULTILINE)
_DOWN_REVISION_ASSIGNMENT_PATTERN = re.compile(r'^(down_revision\s*=\s*)("([^"]+)"|None)$', re.MULTILINE)
_DOC_REVISION_PATTERN = re.compile(r"^(Revision ID:\s*)(\S+)$", re.MULTILINE)
_DOC_REVISES_PATTERN = re.compile(r"^(Revises:\s*)(\S*)$", re.MULTILINE)


def _versions_dir() -> Path:
    return (Path(__file__).resolve().parents[1] / "app" / "db" / "alembic" / "versions").resolve()


def _rewrite_migration_content(content: str) -> str:
    def _replace_revision(match: re.Match[str]) -> str:
        current = match.group(2)
        updated = OLD_TO_NEW_REVISION_MAP.get(current, current)
        return f"{match.group(1)}{updated}{match.group(3)}"

    def _replace_down_revision(match: re.Match[str]) -> str:
        value = match.group(2)
        if value == "None":
            return match.group(0)
        current = match.group(3) or ""
        updated = OLD_TO_NEW_REVISION_MAP.get(current, current)
        return f'{match.group(1)}"{updated}"'

    def _replace_doc_revision(match: re.Match[str]) -> str:
        current = match.group(2)
        updated = OLD_TO_NEW_REVISION_MAP.get(current, current)
        return f"{match.group(1)}{updated}"

    def _replace_doc_revises(match: re.Match[str]) -> str:
        current = match.group(2)
        if not current:
            return match.group(0)
        updated = OLD_TO_NEW_REVISION_MAP.get(current, current)
        return f"{match.group(1)}{updated}"

    rewritten = _REVISION_ASSIGNMENT_PATTERN.sub(_replace_revision, content)
    rewritten = _DOWN_REVISION_ASSIGNMENT_PATTERN.sub(_replace_down_revision, rewritten)
    rewritten = _DOC_REVISION_PATTERN.sub(_replace_doc_revision, rewritten)
    rewritten = _DOC_REVISES_PATTERN.sub(_replace_doc_revises, rewritten)
    return rewritten


def _validate_chain(versions_dir: Path) -> tuple[str, ...]:
    files = sorted(path for path in versions_dir.glob("*.py") if path.name != "__init__.py")
    revisions: dict[str, str | None] = {}
    down_targets: set[str] = set()
    errors: list[str] = []

    for path in files:
        content = path.read_text(encoding="utf-8")
        revision_match = _REVISION_ASSIGNMENT_PATTERN.search(content)
        down_revision_match = _DOWN_REVISION_ASSIGNMENT_PATTERN.search(content)
        if revision_match is None:
            errors.append(f"Missing revision assignment in {path.name}")
            continue
        if down_revision_match is None:
            errors.append(f"Missing down_revision assignment in {path.name}")
            continue

        revision = revision_match.group(2)
        down_revision_literal = down_revision_match.group(2)
        if down_revision_literal == "None":
            down_revision = None
        else:
            down_revision = down_revision_match.group(3)
            if down_revision is None:
                errors.append(f"Invalid down_revision literal in {path.name}")
                continue

        if revision in revisions:
            errors.append(f"Duplicate revision id {revision}")
            continue
        revisions[revision] = down_revision
        if down_revision is not None:
            down_targets.add(down_revision)

        expected_filename = f"{revision}.py"
        if path.name != expected_filename:
            errors.append(f"Filename mismatch for revision {revision}: expected {expected_filename}, got {path.name}")

    for revision, down_revision in revisions.items():
        if down_revision is not None and down_revision not in revisions:
            errors.append(f"Missing down_revision target {down_revision} referenced by {revision}")

    roots = [revision for revision, down in revisions.items() if down is None]
    if len(roots) != 1:
        errors.append(f"Expected exactly one root revision, found {len(roots)}")

    heads = [revision for revision in revisions if revision not in down_targets]
    if len(heads) != 1:
        errors.append(f"Expected exactly one head revision, found {len(heads)}")

    visited: set[str] = set()
    for revision in revisions:
        chain_seen: set[str] = set()
        current = revision
        while True:
            if current in chain_seen:
                errors.append(f"Cycle detected at revision {current}")
                break
            chain_seen.add(current)
            down = revisions.get(current)
            if down is None:
                break
            current = down
        visited |= chain_seen

    if len(visited) != len(revisions):
        missing = sorted(set(revisions) - visited)
        errors.append(f"Unreachable revisions detected: {', '.join(missing)}")

    return tuple(errors)


def _rewrite_files(versions_dir: Path, *, apply: bool) -> None:
    for old_revision, new_revision in OLD_TO_NEW_REVISION_MAP.items():
        old_path = versions_dir / f"{old_revision}.py"
        new_path = versions_dir / f"{new_revision}.py"

        if not old_path.exists():
            if new_path.exists():
                continue
            raise RuntimeError(f"Missing source migration file: {old_path}")

        content = old_path.read_text(encoding="utf-8")
        rewritten = _rewrite_migration_content(content)

        if not apply:
            print(f"[dry-run] rewrite {old_path.name} -> {new_path.name}")
            continue

        if new_path.exists() and new_path != old_path:
            raise RuntimeError(f"Target file already exists: {new_path}")

        new_path.write_text(rewritten, encoding="utf-8")
        if old_path != new_path:
            old_path.unlink()
            print(f"[apply] rewrite {old_path.name} -> {new_path.name}")
        else:
            print(f"[apply] rewrite {old_path.name}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rewrite Alembic revision IDs to timestamp-based format.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag, script runs in dry-run mode.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    versions_dir = _versions_dir()
    _rewrite_files(versions_dir, apply=args.apply)
    if not args.apply:
        return

    errors = _validate_chain(versions_dir)
    if errors:
        print("migration_chain_validation=failed")
        for error in errors:
            print(error)
        raise SystemExit(1)
    print("migration_chain_validation=ok")


if __name__ == "__main__":
    main()
