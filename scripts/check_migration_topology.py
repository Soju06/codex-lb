#!/usr/bin/env python3
"""Alembic revision-graph fitness checks (author-time fork guard).

Run by ``make lint`` (architecture-check). Stdlib only: it reads
``app/db/alembic/versions/*.py`` with ``ast`` instead of booting Alembic, so it
needs neither a database URL nor an importable app, and it runs in the Lint job
on every backend PR.

Why it exists: on 2026-09-11 ``20260910_010000_add_dashboard_auth_providers``
and ``20260910_010000_dashboard_spool_retention`` were authored on different
branches, took the *same* timestamp slot, and descended from different parents.
Both PRs were green, and once both landed on ``main`` every job that migrates a
database failed with ``alembic.script.revision.MultipleHeads`` (Playwright, Helm
smoke, unit, all integration-core shards, both PostgreSQL suites) until #2368
(``53f968723``) added an empty merge revision. Nothing surfaced the fork while
the PRs were open: ``_collect_migration_policy_violations`` in
``app/db/migrate.py`` reports ``alembic_head_count_invalid`` only when both
parents already sit in one tree, and CI is not re-triggered when the base
branch advances under an already-green head.

Checks:

1. The revision graph is one connected acyclic lineage: exactly one head, exactly
   one base, no duplicate ids, no ``down_revision`` naming a revision that does
   not exist, no cycle, and nothing the head does not descend from (error). Same
   invariant as ``alembic_head_count_invalid`` plus the shapes the head and base
   counts cannot see, minus the database: ``make lint`` on a branch that is
   merged/rebased onto current ``main`` fails before the push.
2. No two revisions share a ``YYYYMMDD_HHMMSS`` timestamp prefix (error,
   ratcheted -- see ``RATCHET_PREFIX``). The collision is the authoring-time
   fingerprint of the incident: two authors picking the same slot means either
   the graph forks (different parents) or filename order no longer implies
   graph order (chained). The message names both revisions with their
   ``down_revision``s so the fork is visible without opening the files.
3. A revision's id matches its filename stem and the shared revision-id format
   (error, whole history). Mirrors the runtime policy's
   ``alembic_revision_filename_mismatch`` / ``alembic_revision_id_format_invalid``
   so a mismatch fails in Lint instead of only in the migrating jobs.
4. A revision's timestamp prefix is not older than its parent's (warning,
   ratcheted). Order inversions are legal for Alembic but make the filename
   ordering lie; they are a *warning*, not an error, because the common cause is
   the sanctioned "re-point ``down_revision`` after main moved" rebase and the
   fix (renaming the revision) is only free before the revision ships.
5. Branch fork against a base ref (error, skipped when the ref is absent): a
   revision this checkout adds on top of ``--base-ref`` must descend from a head
   of that ref, not from a revision the base ref has already built on. The base
   ref's own ``down_revision`` edges are read out of git (two plumbing calls, no
   network), because a branch that has not rebased does not contain the revision
   ``main`` added -- only ``main``'s copy can prove the parent is no longer its
   head. This is the only check that sees the fork from the *branch alone*,
   before a rebase or a merge ref brings both parents into one tree. Merge
   revisions (more than one parent) are exempt: merging two heads is exactly how
   a fork is repaired. Skipped in CI, where the checkout has no ``origin/main``
   -- there the ``pull_request`` merge ref makes check 1 equivalent.

Exit codes: 0 = clean (warnings allowed), 1 = violations, 2 = config error.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = ROOT / "app" / "db" / "alembic" / "versions"
VERSIONS_RELATIVE = "app/db/alembic/versions"
DEFAULT_BASE_REF = "origin/main"

# Same shape as app.db.alembic.revision_ids.REVISION_ID_PATTERN, duplicated so the
# script stays stdlib-only (it must run before/without the app environment).
REVISION_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[a-z0-9_]+$")
TIMESTAMP_PREFIX_PATTERN = re.compile(r"^(\d{8}_\d{6})_")

# Checks 2 and 4 are forward-only ratchets: they apply to a group only when at
# least one member is stamped at or after this prefix. The history that predates
# the guard carries 36 prefix-collision groups and 25 order inversions from
# months of parallel branches, and a revision id cannot be renamed once it has
# been stamped into deployed databases, so the alternative was a 36-entry
# allowlist that says nothing about new work. A cutoff needs no allowlist and
# cannot go stale: every new revision gets a fresh timestamp, so it lands on the
# enforced side by construction. Verified at 20260911_010000 (the incident's
# merge revision): zero violations at or after the cutoff.
RATCHET_PREFIX = "20260911_000000"

_FAILURE_PREFIX = "check_migration_topology"


@dataclass(frozen=True, slots=True)
class Revision:
    """One ``versions/*.py`` file, reduced to its graph edges."""

    revision: str
    down_revisions: tuple[str, ...]
    filename: str
    depends_on: bool = False

    @property
    def prefix(self) -> str | None:
        match = TIMESTAMP_PREFIX_PATTERN.match(self.revision)
        return match.group(1) if match else None

    def describe(self) -> str:
        parents = ", ".join(self.down_revisions) if self.down_revisions else "None (base)"
        return f"{self.revision} (down_revision={parents})"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _literal(node: ast.expr, filename: str, name: str) -> object:
    try:
        return ast.literal_eval(node)
    except ValueError as exc:
        raise ValueError(f"{filename}: {name} is not a literal ({exc})") from exc


def _module_assignments(source: str, filename: str) -> dict[str, ast.expr]:
    try:
        module = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ValueError(f"{filename}: cannot parse ({exc.msg} at line {exc.lineno})") from exc
    assignments: dict[str, ast.expr] = {}
    for node in module.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assignments[node.target.id] = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments[node.targets[0].id] = node.value
    return assignments


def parse_revision(source: str, filename: str) -> Revision:
    """Parse ``revision`` / ``down_revision`` / ``depends_on`` out of a migration module."""
    assignments = _module_assignments(source, filename)
    if "revision" not in assignments:
        raise ValueError(f"{filename}: no module-level 'revision' assignment")
    revision = _literal(assignments["revision"], filename, "revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError(f"{filename}: revision must be a non-empty string, got {revision!r}")

    down: object = None
    if "down_revision" in assignments:
        down = _literal(assignments["down_revision"], filename, "down_revision")
    if down is None:
        down_revisions: tuple[str, ...] = ()
    elif isinstance(down, str):
        down_revisions = (down,)
    elif isinstance(down, (tuple, list)) and all(isinstance(item, str) for item in down):
        down_revisions = tuple(down)
    else:
        raise ValueError(f"{filename}: down_revision must be a string, a sequence of strings, or None")

    depends_on: object = None
    if "depends_on" in assignments:
        depends_on = _literal(assignments["depends_on"], filename, "depends_on")

    return Revision(
        revision=revision,
        down_revisions=down_revisions,
        filename=filename,
        depends_on=depends_on is not None,
    )


def load_graph(versions_dir: Path) -> tuple[Revision, ...]:
    """Every revision under ``versions_dir``, ordered by filename."""
    if not versions_dir.is_dir():
        raise ValueError(f"{versions_dir}: not a directory")
    revisions: list[Revision] = []
    for path in sorted(versions_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        revisions.append(parse_revision(path.read_text(encoding="utf-8"), path.name))
    if not revisions:
        raise ValueError(f"{versions_dir}: no revision files found")
    return tuple(revisions)


def graph_heads(revisions: Iterable[Revision]) -> tuple[str, ...]:
    """Revision ids nothing else descends from (Alembic's heads)."""
    known = {revision.revision for revision in revisions}
    parents = {parent for revision in revisions for parent in revision.down_revisions}
    return tuple(sorted(known - parents))


def cyclic_revisions(revisions: Sequence[Revision]) -> tuple[str, ...]:
    """Revision ids Alembic could never order: a cycle, or a self-reference.

    Kahn peeling over the ``down_revision`` edges between *known* revisions
    (dangling parents are reported separately, and would otherwise pin every
    descendant here). Whatever cannot be peeled is in or below a cycle.
    """
    known = {revision.revision for revision in revisions}
    parents = {
        revision.revision: tuple(parent for parent in revision.down_revisions if parent in known)
        for revision in revisions
    }
    children: dict[str, list[str]] = defaultdict(list)
    indegree = {revision_id: len(revision_parents) for revision_id, revision_parents in parents.items()}
    for revision_id, revision_parents in parents.items():
        for parent in revision_parents:
            children[parent].append(revision_id)
    pending = deque(revision_id for revision_id, degree in indegree.items() if degree == 0)
    while pending:
        revision_id = pending.popleft()
        for child in children.get(revision_id, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
    return tuple(sorted(revision_id for revision_id, degree in indegree.items() if degree > 0))


def unreachable_revisions(revisions: Sequence[Revision]) -> tuple[str, ...]:
    """Revision ids no head descends from: a disconnected lineage Alembic never walks."""
    parents = {revision.revision: revision.down_revisions for revision in revisions}
    reachable: set[str] = set()
    pending = list(graph_heads(revisions))
    while pending:
        revision_id = pending.pop()
        if revision_id in reachable:
            continue
        reachable.add(revision_id)
        pending.extend(parents.get(revision_id, ()))
    return tuple(sorted(set(parents) - reachable))


def check_graph_shape(revisions: Sequence[Revision]) -> Report:
    """One head, one base, one connected acyclic lineage, no dangling ids."""
    report = Report()
    by_id: dict[str, list[Revision]] = defaultdict(list)
    for revision in revisions:
        by_id[revision.revision].append(revision)
    for revision_id, group in sorted(by_id.items()):
        if len(group) > 1:
            files = ", ".join(sorted(item.filename for item in group))
            report.error(f"alembic_revision_duplicate revision={revision_id} files={files}")

    for revision in revisions:
        for parent in revision.down_revisions:
            if parent not in by_id:
                report.error(
                    f"{revision.filename}: down_revision={parent!r} names no revision in "
                    f"{VERSIONS_RELATIVE}; point it at an existing revision"
                )
        if revision.depends_on:
            report.warn(
                f"{revision.filename}: depends_on is set; this guard models only down_revision edges, "
                "so extend it if depends_on becomes part of the graph"
            )

    heads = graph_heads(revisions)
    if len(heads) > 1:
        described = "; ".join(by_id[head][0].describe() for head in heads if head in by_id)
        report.error(
            f"alembic_head_count_invalid expected=1 actual={len(heads)} heads={','.join(heads)}: "
            f"the revision graph forks -- {described}. Every job that migrates a database will fail with "
            "MultipleHeads. Re-point the newer revision's down_revision at the other head, or add a merge "
            "revision (alembic merge heads) when both are already released."
        )
    elif not heads:
        report.error(f"{VERSIONS_RELATIVE}: the revision graph has no head (every revision has a descendant)")

    bases = sorted(revision.revision for revision in revisions if not revision.down_revisions)
    if len(bases) != 1:
        detail = (
            "a second down_revision=None revision starts a disconnected lineage"
            if len(bases) > 1
            else "no revision has down_revision=None, so the lineage has no starting point (usually a cycle)"
        )
        report.error(
            f"alembic_base_count_invalid expected=1 actual={len(bases)} bases={','.join(bases) or 'none'}: {detail}"
        )

    # Head and base counts alone accept a cycle and a disconnected component: a
    # self-referencing or mutually-referencing pair is nobody's head and nobody's
    # base, so it hides between the two counts while Alembic refuses to traverse it.
    cyclic = cyclic_revisions(revisions)
    if cyclic:
        report.error(
            f"alembic_revision_cycle revisions={','.join(cyclic)}: these revisions cannot be ordered because "
            "their down_revision edges form a cycle (or a self-reference); Alembic cannot walk them during an "
            "upgrade. Re-point each down_revision at the revision that really precedes it."
        )
    unreachable = unreachable_revisions(revisions)
    if unreachable:
        report.error(
            f"alembic_revision_unreachable revisions={','.join(unreachable)}: no head descends from these "
            f"revisions, so `alembic upgrade head` never applies them. Attach them to the lineage that ends at "
            f"{','.join(heads) or 'the head'} or delete them."
        )
    return report


def check_revision_identity(revisions: Iterable[Revision]) -> Report:
    """Revision id must equal the filename stem and match the shared id format."""
    report = Report()
    for revision in revisions:
        expected_name = f"{revision.revision}.py"
        if revision.filename != expected_name:
            report.error(
                f"alembic_revision_filename_mismatch revision={revision.revision} "
                f"expected={expected_name} actual={revision.filename}: the id and the filename must agree so "
                "the timestamp prefix on disk is the revision's real slot"
            )
        if not REVISION_ID_PATTERN.fullmatch(revision.revision):
            report.error(
                f"alembic_revision_id_format_invalid revision={revision.revision}: "
                "ids are '<YYYYMMDD>_<HHMMSS>_<lower_snake_case>'"
            )
    return report


def _ratcheted(prefixes: Iterable[str | None], ratchet_prefix: str) -> bool:
    return any(prefix is not None and prefix >= ratchet_prefix for prefix in prefixes)


def _ancestors(revision_id: str, parents: Mapping[str, tuple[str, ...]]) -> set[str]:
    seen: set[str] = set()
    pending = list(parents.get(revision_id, ()))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(parents.get(current, ()))
    return seen


def _group_is_chained(group: Sequence[Revision], parents: Mapping[str, tuple[str, ...]]) -> bool:
    """True when every member of ``group`` is an ancestor or descendant of every other."""
    ancestors = {revision.revision: _ancestors(revision.revision, parents) for revision in group}
    return all(
        left.revision in ancestors[right.revision] or right.revision in ancestors[left.revision]
        for index, left in enumerate(group)
        for right in group[index + 1 :]
    )


def check_timestamp_prefix_collisions(revisions: Sequence[Revision], ratchet_prefix: str = RATCHET_PREFIX) -> Report:
    """Two revisions in the same timestamp slot: the incident's authoring-time fingerprint."""
    report = Report()
    parents = {revision.revision: revision.down_revisions for revision in revisions}
    by_prefix: dict[str, list[Revision]] = defaultdict(list)
    for revision in revisions:
        prefix = revision.prefix
        if prefix is not None:
            by_prefix[prefix].append(revision)
    for prefix, group in sorted(by_prefix.items()):
        if len(group) < 2:
            continue
        if not _ratcheted((prefix,), ratchet_prefix):
            continue
        group = sorted(group, key=lambda item: item.revision)
        described = "; ".join(revision.describe() for revision in group)
        forked = not _group_is_chained(group, parents)
        consequence = (
            "they sit on different lineages, so the graph forks and every job that migrates a database "
            "fails with MultipleHeads once both land"
            if forked
            else "they are chained, so filename order no longer tells you the graph order"
        )
        report.error(
            f"alembic_timestamp_prefix_collision prefix={prefix} count={len(group)}: {described}. "
            f"{len(group)} revisions took the same timestamp slot, which means they were authored in parallel: "
            f"{consequence}. Re-stamp the newer revision with a fresh <YYYYMMDD>_<HHMMSS> "
            "(scripts/rewrite_alembic_revisions.py) and re-point its down_revision at the current head."
        )
    return report


def check_prefix_ordering(revisions: Iterable[Revision], ratchet_prefix: str = RATCHET_PREFIX) -> Report:
    """Warn when a revision's timestamp prefix is older than a parent's."""
    report = Report()
    prefixes = {revision.revision: revision.prefix for revision in revisions}
    for revision in revisions:
        prefix = revision.prefix
        if prefix is None or not _ratcheted((prefix,), ratchet_prefix):
            continue
        for parent in revision.down_revisions:
            parent_prefix = prefixes.get(parent)
            if parent_prefix is not None and prefix < parent_prefix:
                report.warn(
                    f"alembic_revision_order_inverted revision={revision.revision} parent={parent}: "
                    f"the revision's timestamp ({prefix}) precedes its parent's ({parent_prefix}), so sorting "
                    "the versions directory by filename no longer matches the upgrade order; re-stamp the "
                    "revision with a current timestamp while it is still unreleased"
                )
    return report


def _git(*args: str, stdin: str | None = None) -> str | None:
    """Run git in the repository; ``None`` when git or the object is unavailable."""
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=ROOT,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _blob_entries(base_ref: str) -> list[tuple[str, str]] | None:
    """``(blob sha, filename)`` for every revision file in ``base_ref``."""
    listing = _git("ls-tree", "-r", base_ref, "--", VERSIONS_RELATIVE)
    if listing is None:
        return None
    entries: list[tuple[str, str]] = []
    for line in listing.splitlines():
        metadata, _, path = line.partition("\t")
        fields = metadata.split()
        if len(fields) != 3 or fields[1] != "blob" or not path:
            continue
        name = Path(path).name
        if not name.endswith(".py") or name == "__init__.py":
            continue
        entries.append((fields[2], name))
    return entries


def _blob_contents(entries: Sequence[tuple[str, str]]) -> dict[str, str] | None:
    """Read every blob in one ``git cat-file --batch``; ``None`` if any is missing.

    The batch stream is read as bytes because ``--batch`` headers size each blob
    in bytes; each payload is decoded on its own.
    """
    if not entries:
        return {}
    try:
        completed = subprocess.run(
            ("git", "cat-file", "--batch"),
            cwd=ROOT,
            input="\n".join(sha for sha, _ in entries).encode("ascii") + b"\n",
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    batch = completed.stdout
    contents: dict[str, str] = {}
    position = 0
    for sha, _ in entries:
        newline = batch.find(b"\n", position)
        if newline < 0:
            return None
        header = batch[position:newline].split()
        if len(header) != 3 or header[0].decode("ascii", "replace") != sha:
            return None
        start = newline + 1
        end = start + int(header[2])
        try:
            contents[sha] = batch[start:end].decode("utf-8")
        except UnicodeDecodeError:
            return None
        position = end + 1
    return contents


def base_ref_revisions(base_ref: str) -> tuple[Revision, ...] | None:
    """The revision graph as ``base_ref`` has it, or ``None`` when it is unreadable.

    The base ref's own ``down_revision`` edges are required, not just its ids: a
    branch that has not rebased does not contain the revision ``main`` added, so
    only ``main``'s copy can say that a parent is no longer ``main``'s head.
    """
    if _git("rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}") is None:
        return None
    entries = _blob_entries(base_ref)
    if not entries:
        return None
    contents = _blob_contents(entries)
    if contents is None:
        return None
    revisions: list[Revision] = []
    for sha, name in entries:
        source = contents.get(sha)
        if source is None:
            return None
        try:
            revisions.append(parse_revision(source, name))
        except ValueError:
            # History we do not control: degrade to "check skipped" rather than
            # failing a branch over a file it did not touch.
            return None
    return tuple(revisions)


def check_branch_fork(revisions: Sequence[Revision], base_revisions: Sequence[Revision], base_ref: str) -> Report:
    """A revision this branch adds must descend from a head of ``base_ref``.

    Both graphs are needed. A parent that ``base_ref`` does not know is skipped
    (it is a branch-local revision, or the ref is behind the checkout), and a
    merge revision is exempt because merging two heads is the sanctioned repair.
    """
    report = Report()
    base_ids = {revision.revision for revision in base_revisions}
    base_children: dict[str, list[str]] = defaultdict(list)
    for revision in base_revisions:
        for parent in revision.down_revisions:
            base_children[parent].append(revision.revision)
    base_heads = tuple(sorted(base_ids - set(base_children)))
    for revision in revisions:
        if revision.revision in base_ids:
            continue
        if len(revision.down_revisions) != 1:
            # A merge revision legitimately names the heads it repairs, and a new
            # base revision (no parent) is caught by check_graph_shape instead.
            continue
        parent = revision.down_revisions[0]
        if parent not in base_ids or parent not in base_children:
            continue
        report.error(
            f"alembic_branch_forks_base revision={revision.revision} parent={parent} base_ref={base_ref}: "
            f"{base_ref} already builds on {parent} ({', '.join(sorted(base_children[parent]))}), so this "
            f"branch's revision starts a second lineage and merging it produces two Alembic heads. Rebase onto "
            f"{base_ref} (head{'s' if len(base_heads) != 1 else ''}: {', '.join(base_heads) or 'unknown'}) and "
            f"re-point down_revision -- and re-stamp {revision.revision} if its timestamp is now in the past."
        )
    return report


def run_all(
    versions_dir: Path = VERSIONS_DIR,
    base_ref: str = DEFAULT_BASE_REF,
    base_revisions: Sequence[Revision] | None = None,
    ratchet_prefix: str = RATCHET_PREFIX,
) -> tuple[list[Report], str]:
    revisions = load_graph(versions_dir)
    reports = [
        check_graph_shape(revisions),
        check_revision_identity(revisions),
        check_timestamp_prefix_collisions(revisions, ratchet_prefix),
        check_prefix_ordering(revisions, ratchet_prefix),
    ]
    heads = graph_heads(revisions)
    base_note = "skipped (ref unavailable)"
    if base_ref:
        if base_revisions is None:
            base_revisions = base_ref_revisions(base_ref)
        if base_revisions is not None:
            reports.append(check_branch_fork(revisions, base_revisions, base_ref))
            base_ids = {revision.revision for revision in base_revisions}
            added = len({revision.revision for revision in revisions} - base_ids)
            base_note = f"{base_ref} (+{added} revision(s) on this checkout)"
    else:
        base_note = "disabled"
    summary = (
        f"revisions: {len(revisions)}; head: {','.join(heads) or 'none'}; "
        f"ratchet: {ratchet_prefix}; base-ref check: {base_note}"
    )
    return reports, summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail migration authoring when the Alembic revision graph forks.")
    parser.add_argument(
        "--versions-dir",
        type=Path,
        default=VERSIONS_DIR,
        help="alembic versions directory to check (default: this checkout's)",
    )
    parser.add_argument(
        "--base-ref",
        default=DEFAULT_BASE_REF,
        help=f"git ref the branch builds on; empty disables the branch-fork check (default: {DEFAULT_BASE_REF})",
    )
    arguments = parser.parse_args(argv)

    try:
        reports, summary = run_all(versions_dir=arguments.versions_dir, base_ref=arguments.base_ref)
    except (OSError, ValueError) as exc:
        print(f"{_FAILURE_PREFIX}: config error: {exc}", file=sys.stderr)
        return 2

    warnings: list[str] = [message for report in reports for message in report.warnings]
    errors: list[str] = [message for report in reports for message in report.errors]
    for message in warnings:
        print(f"WARN: {message}")
    for message in errors:
        print(f"ERROR: {message}")
    print(summary)
    if errors:
        print(f"{_FAILURE_PREFIX}: {len(errors)} violation(s)")
        return 1
    print("migration topology checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
