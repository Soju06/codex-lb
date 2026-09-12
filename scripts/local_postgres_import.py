"""Copy a consistent SQLite snapshot into an empty, pre-migrated PostgreSQL database.

Read LOCAL_POSTGRES_URL from the environment; never accept credentials in argv.
An online copy is a rehearsal, not a write fence or authorization to cut over.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Connection


def canonical(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    return value


def fingerprint(connection: Connection, table: sa.Table) -> dict[str, str | int]:
    hashes = []
    for row in connection.execute(sa.select(table)).mappings():
        encoded = json.dumps(
            {key: canonical(value) for key, value in row.items()}, sort_keys=True, separators=(",", ":")
        ).encode()
        hashes.append(hashlib.sha256(encoded).digest())
    return {"rows": len(hashes), "sha256": hashlib.sha256(b"".join(sorted(hashes))).hexdigest()}


def copy_rows(source: Connection, target: Connection) -> dict:
    source_meta, target_meta = sa.MetaData(), sa.MetaData()
    source_meta.reflect(source)
    target_meta.reflect(target)
    if source_meta.tables.keys() != target_meta.tables.keys():
        raise ValueError("Source and target table sets differ")
    for name, table in source_meta.tables.items():
        if set(table.columns.keys()) != set(target_meta.tables[name].columns.keys()):
            raise ValueError(f"Column sets differ: {name}")
    tables = target_meta.sorted_tables
    quote = target.dialect.identifier_preparer.quote
    target.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
    target.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
    target.exec_driver_sql("LOCK TABLE " + ", ".join(quote(t.name) for t in tables) + " IN ACCESS EXCLUSIVE MODE")
    for table in tables:
        if table.name != "alembic_version" and target.execute(sa.select(table).limit(1)).first() is not None:
            raise ValueError(f"Target is not empty: {table.name}")
    revision = sa.text("SELECT version_num FROM alembic_version")
    if source.execute(revision).all() != target.execute(revision).all():
        raise ValueError("Alembic revisions differ")
    report = {}
    for table in tables:
        original = source_meta.tables[table.name]
        if table.name != "alembic_version":
            for rows in source.execute(sa.select(original)).mappings().partitions(500):
                target.execute(table.insert(), [dict(row) for row in rows])
        expected = fingerprint(source, original)
        if fingerprint(target, table) != expected:
            raise ValueError(f"Row checksum mismatch: {table.name}")
        report[table.name] = expected
    # Sequence state is not transactional in PostgreSQL. Only unused target
    # sequences are changed, after all row verification; retries reset them.
    for table in tables:
        for column in table.columns:
            sequence = target.execute(
                sa.text("SELECT pg_get_serial_sequence(:table, :column)"),
                {"table": quote(table.name), "column": column.name},
            ).scalar()
            if sequence:
                maximum = target.execute(sa.select(sa.func.max(column))).scalar()
                target.execute(
                    sa.text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                    {"sequence": sequence, "value": max(maximum or 1, 1), "called": maximum is not None},
                )
    return report


def migrate(source_path: Path, target_url: str) -> dict:
    source_path = source_path.resolve(strict=True)
    if sa.engine.make_url(target_url).get_backend_name() != "postgresql":
        raise ValueError("Target must be PostgreSQL")
    with tempfile.TemporaryDirectory(prefix="codex-lb-import-") as directory:
        snapshot = Path(directory) / "snapshot.db"
        started = time.monotonic()

        def deadline(status, remaining, total):
            if time.monotonic() - started > 120:
                raise TimeoutError("SQLite snapshot deadline exceeded")

        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(snapshot) as backup:
                source.backup(backup, pages=1024, progress=deadline)
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("SQLite integrity check failed")
                if backup.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("SQLite foreign key check failed")
        source_engine = sa.create_engine("sqlite:///" + str(snapshot), hide_parameters=True)
        target_engine = sa.create_engine(target_url, hide_parameters=True, connect_args={"connect_timeout": 5})
        try:
            with source_engine.connect() as source, target_engine.begin() as target:
                return copy_rows(source, target)
        finally:
            source_engine.dispose()
            target_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    with args.report.open("x") as output:
        os.chmod(args.report, 0o600)
        try:
            result = migrate(args.source, os.environ["LOCAL_POSTGRES_URL"])
        except Exception as error:
            # Database exceptions may include row contents or URLs; keep them private.
            raise SystemExit(f"Import failed ({type(error).__name__}); target rows rolled back") from None
        try:
            json.dump(result, output, indent=2)
        except OSError:
            raise SystemExit("Import committed, but report write failed; do not repeat import") from None
    print(f"Verified {len(result)} tables; report: {args.report}")
