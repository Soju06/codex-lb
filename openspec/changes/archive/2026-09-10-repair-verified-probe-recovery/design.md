## Context

The failed startup tests use current metadata without an Alembic stamp, a bootstrap path supported by the existing migrations. The new revision blindly adds columns. Replica failure recording advances a generation before clearing recovered holds.

## Goals / Non-Goals

Preserve the accepted completed-probe contract and existing test assertions. No routing-policy changes, new settings, periodic probes or live operations.

## Decisions

Share generation reconciliation between selection and direct failure recording. A newer recovered row clears stale holds before the generation advances; equal or older rows cannot clear newer local evidence.

Follow existing migration column inspection for the unmerged revision. Changing fixtures to stamp head would hide the supported startup failure. Existing column values remain untouched.

Set both test database variables from the fixture's existing disposable default before imports. Keep engine identity assertions and update the obsolete probe stub to the typed contract.

## Risks / Trade-offs

Clearing newer local rejections would violate recovery safety. Keep the older-row regression and exercise the public probe-to-routing path with an intervening transient error.

## Migration Plan

Verify historical rows, partial columns and startup on disposable SQLite and PostgreSQL. The original reversible revision remains unmerged; no additional revision or live migration is needed.
