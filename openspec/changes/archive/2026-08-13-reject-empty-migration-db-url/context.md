## Purpose and Scope

This change closes an operator-safety gap in the standalone migration CLI. Its normative behavior is defined in `specs/database-migrations/spec.md`; this context explains why the parser boundary and subprocess coverage were chosen.

The affected surface is CLI target selection for all six supported migration subcommands. Migration mechanics and the settings model remain outside the change.

## Decision Rationale

An empty argv element is real input: scripts commonly invoke `--db-url "$DATABASE_URL"`, and an unset shell variable preserves an exact empty value when quoted. Treating that value as omission can redirect a destructive command to the configured or default database.

Validation belongs in `argparse` because parsing completes before settings are constructed. A narrow supplied-value converter separates exact empty from the `None` omission sentinel. An explicit identity check at target resolution documents and preserves that distinction.

Validating later, trimming values, or changing global settings validation were considered and rejected. Those options either allow fallback resolution first or expand the contract beyond the reproduced defect.

## Constraints and Non-Goals

- Existing deployment jobs omit `--db-url` and provide `CODEX_LB_DATABASE_URL`; that flow must continue unchanged.
- Non-empty values pass through without normalization or new syntax checks.
- Whitespace-only input and an empty global database setting retain existing behavior.
- The change introduces no Alembic revision, settings field, deployment edit, or dashboard-visible output.

## Failure Modes and Edge Cases

- `upgrade` and `stamp` can durably change a wrongly selected database, so rejection must happen before command dispatch.
- `current`, `check`, and both wait commands can open or create SQLite files even when their final exit status is nonzero; tests must inspect the filesystem rather than infer safety from exit status alone.
- Shell calls that omit the value token entirely are handled by normal `argparse` missing-value errors and are not the reproduced exact-empty case.

## Concrete Operator Flow

A quoted empty variable such as `python -m app.db.migrate --db-url "$DATABASE_URL" upgrade head` must fail as an argument error when `DATABASE_URL` is empty. The same command without the `--db-url` option continues to resolve the configured settings target and migrate it.

## Operational and Verification Notes

The regression runs each supported subcommand in a fresh child process with a fresh configured fallback path. The explicit-empty matrix proves no fallback file is created or mutated; a separate omitted-option control proves the configured target reaches the current migration head.

Related contracts: the main `database-migrations` capability owns migration CLI and deployment behavior, while database setting provision remains owned by existing configuration/backend contracts.
