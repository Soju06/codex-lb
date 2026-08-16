## Context

Request-log grouping currently takes the first whitespace-delimited token before
splitting on `/`. Consequently, a header such as `Codex Desktop/0.142.4` is
recorded with the family `Codex`, although the delimiter-defined family is
`Codex Desktop`. Historical `request_logs.useragent_group` values require the
same correction.

## Goals / Non-Goals

**Goals:**

- Derive a user-agent family from the complete substring before the first `/`.
- Keep slash-free input behavior unchanged.
- Backfill only non-null stored user-agent values that contain `/`, without
  whitespace trimming or other preprocessing.

**Non-Goals:**

- Parsing arbitrary user-agent grammar beyond the first `/` delimiter.
- Changing the persisted raw `useragent` value.
- Updating stored values with no `/`.

## Decisions

### Use the first slash in the normalized header as the family boundary

The request-log helper will retain its existing missing/blank-header
normalization, then derive the group from the substring before the first `/`,
rather than first splitting on whitespace. This matches the stated
client-family contract for product names containing spaces. Retaining the
slash-free path avoids changing its existing behavior.

Alternative: retain first-token parsing. This is rejected because it truncates
multi-word product names such as `Codex Desktop`.

### Backfill with a delimiter-gated database expression

The Alembic revision will update `useragent_group` only where `useragent` is
non-null and contains `/`, assigning the database substring before that slash.
The expression operates on stored text directly and performs no trim,
normalization, or preprocessing.

Alternative: backfill every non-null row and rely on a substring function's
no-delimiter result. This is rejected because slash-free rows must remain
untouched.

## Risks / Trade-offs

- [Database substring syntax differs by dialect] → Use the migration project's
  established dialect-aware migration pattern and verify upgrade behavior on
  supported test databases.
- [Runtime and historical values have different whitespace treatment] → The
  runtime path retains its established missing/blank-header normalization,
  while the migration derives groups directly from stored text as required.

## Migration Plan

1. Add a revision after the current Alembic head.
2. Update eligible historical rows with the first-slash family expression.
3. Verify the revision upgrades to a single Alembic head and preserves
   slash-free and null rows.
4. Roll back the revision through its downgrade path if deployment must be
   reversed; the stored backfill values may remain because the source
   `useragent` values are unchanged and the correction is repeatable.

## Open Questions

None.
