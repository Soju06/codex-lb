## Why

Issue #566 reports that operators who own multiple Codex accounts under the same email — for example a Plus account on a personal address and a Team account on the same address under a Business workspace — cannot tell them apart in the dashboard listing. Every row shows the same email and the same default `display_name`. There is no operator-controlled identifier to distinguish them.

The existing `Account` model exposes `email` and `chatgpt_account_id`. Neither of those is human-readable, and neither one is operator-controlled. The reporter explicitly asked for a freeform alias that the dashboard can render alongside (or instead of) the email.

## What Changes

- Add a nullable `alias VARCHAR` column to `accounts` via a new alembic migration (`20260513_000000_add_accounts_alias`). Existing rows are unaffected (NULL = "no alias", which is the pre-PR default).
- Surface `alias` on `AccountSummary` so the dashboard listing payload carries it, and make `display_name` fall back to the alias when set (otherwise email) so existing consumers that already render `display_name` pick up the alias without code changes.
- Add a new `PUT /api/accounts/{account_id}/alias` dashboard endpoint guarded by the existing dashboard-session dependency. Body: `{"alias": "..."}` with `max_length=255`. The handler trims whitespace and treats empty/whitespace-only input as a clear (alias → NULL).
- Add a `set_account_alias` method on `AccountsService` and an `update_alias` method on the repository to keep the layering identical to the existing pause/reactivate/delete pattern.
- Add integration regression coverage in `tests/integration/test_accounts_api.py`: 404 on missing account, set-then-list, whitespace trim, and clear via empty body restoring the email fallback.

## Impact

- New API surface only. Existing endpoints, response shapes, and the dashboard SPA continue to work unchanged; `alias` simply becomes available on every account summary and `display_name` becomes alias-aware on the server side.
- Migration is additive and idempotent (early-return when the column already exists, drop on downgrade); safe to run on both SQLite and PostgreSQL backends.
- Frontend UI to set the alias is intentionally out of scope here; that can be added in a follow-up that consumes this endpoint. The server contract is complete on its own.
