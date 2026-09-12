"""Read-only, conservative SQLite gate; no migrations or revision stamping."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from scripts.check_migration_topology import check_graph_shape, graph_heads, load_graph


def release_schema(release: Path) -> tuple[str, str]:
    versions = release / "app/db/alembic/versions"
    graph = load_graph(versions)
    report = check_graph_shape(graph)
    if report.errors:
        raise ValueError("Invalid candidate migration graph")
    digest = hashlib.sha256()
    files = [*sorted(versions.glob("*.py")), release / "app/db/models.py"]
    for path in files:
        digest.update(str(path.relative_to(release)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return graph_heads(graph)[0], digest.hexdigest()


def check(database: Path, active: Path, candidate: Path) -> dict[str, str]:
    database = database.resolve(strict=True)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3) as connection:
        revisions = tuple(row[0] for row in connection.execute("SELECT version_num FROM alembic_version"))
    active_head, active_digest = release_schema(active)
    candidate_head, candidate_digest = release_schema(candidate)
    if revisions != (active_head,) or candidate_head != active_head:
        raise ValueError(
            f"Schema revision mismatch: database={revisions}, active={active_head}, candidate={candidate_head}"
        )
    if active_digest != candidate_digest:
        raise ValueError("Migration code or ORM definitions differ; migration review required")
    return {"revision": active_head, "schema_digest": active_digest}


def check_plan(plan_path: Path, port: int) -> dict[str, str]:
    plan = json.loads(plan_path.read_text())
    # A fixed operator-owned plan, never supplied by a control-socket caller.
    if port != plan["active_port"]:
        raise ValueError("SQLite lifetime lock forbids overlapping backends; use PostgreSQL or a maintenance window")
    candidate = Path(plan["candidates"][str(port)])
    return check(Path(plan["database"]), Path(plan["active_release"]), candidate)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--active", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.database, args.active, args.candidate)))
