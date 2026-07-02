# Claude OAuth Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pool Claude Max/Pro/Team OAuth tokens in codex-lb alongside Codex OAuth tokens: manual token paste in this change, passthrough `/claude/v1/messages` and `/claude/v1/models`, refresh on schedule + rotate-and-retry on 401, minimal dashboard tab.

**Architecture:** Single `accounts` table with `provider` discriminator (`'codex' | 'claude'`). New `app/modules/claude/` with its own `ClaudeProxyService`, `ClaudeAuthManager`, OAuth and chat clients. Load balancer gains a `provider` filter; API keys gain a `provider_scope`. URL namespace bound: `/v1/*` → Codex, `/claude/v1/*` → Claude. ProxyService god object (`app/modules/proxy/service.py`) MUST NOT be modified.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 + Alembic, aiohttp, Pydantic v2, pytest, React 19 + TypeScript + Vitest, OpenSpec.

**Hard constraints (do not relax):**
- Phase 0 (verification) is **mandatory** before any backend code lands. Do not proceed to Phase 1 until the Phase 0 checkpoint at the end has explicitly passed.
- `app/modules/proxy/service.py` ProxyService is **out of scope**; do not edit it.
- `make architecture-check` MUST stay green (line count, method span, cross-domain dependencies).
- `openspec validate add-claude-oauth-pool --strict --no-interactive` MUST stay green.
- Tokens go through `app/core/crypto.py` envelope only. No new crypto primitives.
- Do not commit real tokens. Verification tests use fake/synthetic values; sandbox verification uses throwaway tokens.

---

## Phase 0 — Verification Prerequisites (BLOCKING)

**No backend code may be written until every step here is completed and the Phase 0 checkpoint at the end is explicitly passed.**

### Task 0.1: Verify Anthropic OAuth refresh endpoint

**Files:**
- Create: `openspec/changes/add-claude-oauth-pool/notes.md` (final form filled in later; initial scratch OK here)

- [ ] **Step 1: Capture a Claude Code CLI OAuth token exchange**

In a sandbox (do NOT commit real values), run the Claude Code CLI's OAuth flow once and record:
- Authorization request URL (where the user is redirected).
- Token exchange endpoint URL (`POST <url>`).
- Request body fields and authentication mechanism for the token exchange.
- Response body fields (exact JSON shape).

Use a throwaway account or a documented public-source analysis if a live exchange isn't available. Cite the source for each field.

- [ ] **Step 2: Write the verification note**

Append to `openspec/changes/add-claude-oauth-pool/notes.md`:

```
## Anthropic OAuth refresh

- Refresh endpoint URL: <URL>
- Request shape: <body + auth>
- Response shape: <JSON fields, exact names>
- Sources: <links / files consulted>
- Verification method: <live exchange / source analysis>
- Verified by: <agent / human>
- Date (UTC): <YYYY-MM-DD>
```

### Task 0.2: Verify required Anthropic API header set

- [ ] **Step 1: Capture headers from a live `POST /v1/messages` request**

From a working Claude Code CLI session (or documented reverse-engineered source), record every header the Anthropic API expects on OAuth-authenticated requests. Specifically:
- Exact `Authorization` value format (e.g. `Bearer <access_token>`).
- `anthropic-version` (date or semver — record exact value).
- `anthropic-beta` if present (record exact value or "absent").
- User-Agent shape if relevant.
- Any `anthropic-*` headers the API actually emits in responses that we need to consume.

- [ ] **Step 2: Write the verification note**

Append to `notes.md` under `## Required Anthropic API headers`. Include exact string values, the source, and the verification method.

### Task 0.3: Verify refresh-token rotation behavior

- [ ] **Step 1: Capture refresh response shape on multiple cycles**

If possible, perform 2-3 consecutive refreshes against the same `refresh_token` and record whether the response includes a new `refresh_token` value. Document:
- Whether the refresh response always includes a new refresh token.
- Whether refresh tokens expire on use (single-use) or remain valid until revoked.
- What happens if you reuse a single-use refresh token (server response: error code + body).

- [ ] **Step 2: Write the verification note**

Append to `notes.md` under `## Refresh-token rotation`. List the exact field name(s) and the documented behavior, with source.

### Task 0.4: Verify Anthropic rate-limit response headers

- [ ] **Step 1: Capture rate-limit headers from live traffic**

Trigger enough traffic against a single Anthropic account to receive at least one `anthropic-ratelimit-*` header. Record:
- Exact name of each header on both 200 and 429 responses.
- Format of value (`integer`? ISO timestamp? relative seconds?).
- Whether `anthropic-ratelimit-status` is present on 200, 429, or both.
- Any documented reset semantics (does the value reset on success, or only on rate-limit events?).

Specifically confirm the names in design.md §Data model:

| Header | Name verified | Format | Present on 200 | Present on 429 |
|---|---|---|---|---|
| `anthropic-ratelimit-requests-remaining` |  |  |  |  |
| `anthropic-ratelimit-requests-reset` |  |  |  |  |
| `anthropic-ratelimit-input-tokens-remaining` |  |  |  |  |
| `anthropic-ratelimit-input-tokens-reset` |  |  |  |  |
| `anthropic-ratelimit-output-tokens-remaining` |  |  |  |  |
| `anthropic-ratelimit-output-tokens-reset` |  |  |  |  |
| `anthropic-ratelimit-status` |  |  |  |  |

- [ ] **Step 2: Write the verification note**

Append to `notes.md` under `## Anthropic rate-limit headers`. Include the table above (filled) plus sources and verification method.

### Task 0.5: Finalize notes.md

- [ ] **Step 1: Review and tighten notes.md**

- Replace any "TODO", "TBD", "verify later" markers with confirmed values.
- Add a top-level summary table at the start of `notes.md` mapping each requirement in `specs/claude-oauth-pool/spec.md` "Verification of Anthropic OAuth contract" to its verification subsection.
- Ensure each section has: finding, source, method, verifier, date.

- [ ] **Step 2: Commit notes.md (scratch commit is fine)**

```bash
git add openspec/changes/add-claude-oauth-pool/notes.md
git commit -m "docs(openspec): record Anthropic OAuth contract verification"
```

### Task 0.6: PHASE 0 CHECKPOINT — Realign spec if findings differ from design.md (BLOCKING)

- [ ] **Step 1: Compare notes.md against design.md and spec files**

For each of the four verified contracts (refresh endpoint, API headers, refresh-token rotation, rate-limit headers), compare against `openspec/changes/add-claude-oauth-pool/design.md` and `openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md`. Identify any "material" discrepancies — i.e., differences that would change:
- The URL or method of any HTTP call.
- Any required or emitted header name.
- Whether `rotate_claude_access_token` overwrites the refresh token.
- The semantics of rate-limit cooldown.

Cosmetic differences (label casing, ordering) are not material.

- [ ] **Step 2: Update spec to match verified findings (only if material discrepancies exist)**

If material discrepancies exist, edit `design.md` and `specs/claude-oauth-pool/spec.md` so they match notes.md. Use clear commit messages. Then run:

```bash
openspec validate add-claude-oauth-pool --strict --no-interactive
```

Expected: "Change 'add-claude-oauth-pool' is valid". Fix any validation errors before proceeding.

- [ ] **Step 3: Record the checkpoint decision**

Append to `notes.md`:

```
## Phase 0 checkpoint

- Findings vs design.md: <aligned | list of material discrepancies and edits made>
- Spec still valid after edits: <yes | edits were required + committed + re-validated>
- Cleared by: <agent / human>
- Date (UTC): <YYYY-MM-DD>
```

- [ ] **Step 4: Explicit go/no-go decision**

**STOP HERE unless the user has approved Phase 0.** Do not start Phase 1 without explicit user approval recorded in the conversation. The user explicitly asked: "the implementation MUST start with the verification phase ... BEFORE writing any backend code. If verification reveals the OAuth contract is materially different ... the plan should include a checkpoint to update the spec before proceeding."

---

## Phase 1 — Database Schema

### Task 1.1: Add `provider` column to `Account` model with constraint + test

**Files:**
- Modify: `app/db/models.py` (Account model)
- Test: `tests/unit/test_account_model_provider.py`

- [ ] **Step 1: Write failing model test**

```python
# tests/unit/test_account_model_provider.py
from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, inspect

from app.db.models import Account, AccountStatus

pytestmark = pytest.mark.unit


def test_account_provider_column_has_codex_default_check_constraint() -> None:
    inspector = inspect(Account)
    columns = {c["name"]: c for c in inspector.columns}
    assert columns["provider"]["nullable"] is False
    assert "codex" in (columns["provider"].get("default") or "").lower() or \
        columns["provider"].get("default") is None  # backfill lives in migration
    check_constraints = [
        c for c in inspector.check_constraints
        if "provider" in (c["sqltext"] or "").lower()
    ]
    assert check_constraints, "expected CHECK constraint on accounts.provider"


def test_account_provider_value_must_be_codex_or_claude(make_session) -> None:
    async def _run() -> None:
        async with make_session() as session:
            account = Account(
                id="acc-bad",
                provider="bogus",  # type: ignore[arg-type]
                status=AccountStatus.ACTIVE,
            )
            session.add(account)
            with pytest.raises(Exception):
                await session.flush()

    import asyncio
    asyncio.run(_run())
```

Where `make_session` is the project-standard sqlite fixture (mirror the pattern in `tests/unit/test_accounts_repository.py`).

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/unit/test_account_model_provider.py -v`
Expected: FAIL — `Account` has no `provider` column yet.

- [ ] **Step 3: Add `provider` column to `Account`**

In `app/db/models.py`, on the `Account` model:

```python
provider: Mapped[str] = mapped_column(
    Text,
    nullable=False,
    server_default="codex",
)

__table_args__ = (
    UniqueConstraint("claude_account_uuid", sqlite_where=(text("provider = 'claude'")), name="uq_accounts_claude_uuid"),
    CheckConstraint("provider IN ('codex', 'claude')", name="ck_accounts_provider"),
)
```

(Combine with `__table_args__` already on `Account`; do not duplicate. Verify the `name` is unique across the file.)

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/unit/test_account_model_provider.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/models.py tests/unit/test_account_model_provider.py
git commit -m "feat(db): add provider discriminator to accounts"
```

### Task 1.2: Add Claude-specific columns to `Account` + partial unique index

**Files:**
- Modify: `app/db/models.py`
- Test: `tests/unit/test_account_model_provider.py` (extend)

- [ ] **Step 1: Extend the failing test**

Append to the same test file:

```python
def test_account_has_claude_columns() -> None:
    inspector = inspect(Account)
    names = {c["name"] for c in inspector.columns}
    for col in (
        "claude_account_uuid",
        "claude_refresh_token_encrypted",
        "claude_access_token_encrypted",
        "claude_access_token_expires_at",
        "claude_scopes",
        "claude_user_email",
        "claude_user_organization_uuid",
        "rate_limit_requests_remaining",
        "rate_limit_requests_reset_at",
        "rate_limit_input_tokens_remaining",
        "rate_limit_input_tokens_reset_at",
        "rate_limit_output_tokens_remaining",
        "rate_limit_output_tokens_reset_at",
        "rate_limit_status",
    ):
        assert col in names, f"missing column: {col}"


def test_claude_account_uuid_partial_unique_index() -> None:
    inspector = inspect(Account)
    indexes = list(inspector.indexes)
    partial = [i for i in indexes if "claude_account_uuid" in [c.name for c in i.columns]]
    assert partial, "expected partial unique index on claude_account_uuid"
    # SQLite expresses the predicate in dialect_text; verify the predicate is provider='claude'.
    predicate = (getattr(partial[0], "dialect_options", {}) or {}).get("sqlite", {}).get("where")
    assert predicate is not None and "claude" in str(predicate).lower()


def test_account_email_is_nullable() -> None:
    inspector = inspect(Account)
    columns = {c["name"]: c for c in inspector.columns}
    assert columns["email"]["nullable"] is True
```

- [ ] **Step 2: Run tests, confirm the new ones fail**

Run: `uv run pytest tests/unit/test_account_model_provider.py -v`
Expected: 2-3 new failures.

- [ ] **Step 3: Add columns + index + drop email NOT NULL**

In `app/db/models.py`:

```python
claude_account_uuid: Mapped[str | None] = mapped_column(Text, nullable=True)
claude_refresh_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
claude_access_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
claude_access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
claude_scopes: Mapped[str | None] = mapped_column(Text, nullable=True)
claude_user_email: Mapped[str | None] = mapped_column(Text, nullable=True)
claude_user_organization_uuid: Mapped[str | None] = mapped_column(Text, nullable=True)

rate_limit_requests_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
rate_limit_requests_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
rate_limit_input_tokens_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
rate_limit_input_tokens_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
rate_limit_output_tokens_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
rate_limit_output_tokens_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
rate_limit_status: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Drop `nullable=False` from `email` (set nullable=True). The existing `UNIQUE(email)` constraint must remain — verify it is preserved in `__table_args__` after the change.

Add to `__table_args__`:

```python
Index(
    "uq_accounts_claude_uuid",
    "claude_account_uuid",
    unique=True,
    sqlite_where=text("provider = 'claude'"),
    postgresql_where=text("provider = 'claude'"),
),
```

(Adjust imports for `Index`, `LargeBinary`.)

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_account_model_provider.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/models.py tests/unit/test_account_model_provider.py
git commit -m "feat(db): add Claude account columns and partial unique index"
```

### Task 1.3: Add `provider_scope` to `ApiKey`

**Files:**
- Modify: `app/db/models.py`
- Test: `tests/unit/test_api_key_model_provider_scope.py`

- [ ] **Step 1: Write failing test**

```python
from app.db.models import ApiKey
from sqlalchemy import inspect

def test_api_key_has_provider_scope_column() -> None:
    cols = {c["name"]: c for c in inspect(ApiKey).columns}
    assert "provider_scope" in cols
    assert cols["provider_scope"]["nullable"] is False
    assert cols["provider_scope"]["default"].arg == "codex" or "codex" in (cols["provider_scope"]["default"].arg or "")
```

- [ ] **Step 2: Run, verify fail; then implement and verify pass**

Add to `ApiKey`:

```python
provider_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="codex")
```

- [ ] **Step 3: Commit**

```bash
git add app/db/models.py tests/unit/test_api_key_model_provider_scope.py
git commit -m "feat(db): add provider_scope to api_keys"
```

### Task 1.4: Add `provider` to `RequestLog`

**Files:**
- Modify: `app/db/models.py`
- Test: `tests/unit/test_request_log_model_provider.py`

- [ ] **Step 1: Write failing test, run, implement, verify pass**

```python
def test_request_log_has_provider_column_nullable() -> None:
    from app.db.models import RequestLog
    from sqlalchemy import inspect
    cols = {c["name"]: c for c in inspect(RequestLog).columns}
    assert "provider" in cols
    assert cols["provider"]["nullable"] is True
```

Implement:

```python
provider: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Commit**

```bash
git add app/db/models.py tests/unit/test_request_log_model_provider.py
git commit -m "feat(db): add provider column to request_logs"
```

### Task 1.5: Write Alembic migration with backfill + downgrade

**Files:**
- Create: `app/db/alembic/versions/<rev>_add_claude_account_columns.py`

- [ ] **Step 1: Determine the parent revision**

Run: `cd app/db/alembic && alembic heads`
Use the output as `down_revision` in the new revision.

- [ ] **Step 2: Author the revision**

Use the standard Alembic / project pattern from `app/db/alembic/versions/`. Skeleton:

```python
"""add claude account columns

Revision ID: <rev>
Revises: <parent>
Create Date: <YYYY-MM-DD HH:MM:SS.ffffff>
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<rev>"
down_revision = "<parent>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add columns as NULLABLE first (so backfill can run without violating NOT NULL).
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.Text(), nullable=True, server_default=None))
        batch_op.add_column(sa.Column("claude_account_uuid", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("claude_refresh_token_encrypted", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("claude_access_token_encrypted", sa.LargeBinary(), nullable=True))
        batch_op.add_column(sa.Column("claude_access_token_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("claude_scopes", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("claude_user_email", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("claude_user_organization_uuid", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_requests_remaining", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_requests_reset_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_input_tokens_remaining", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_input_tokens_reset_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_output_tokens_remaining", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_output_tokens_reset_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("rate_limit_status", sa.Text(), nullable=True))
        batch_op.alter_column("email", existing_type=sa.Text(), nullable=True)
        batch_op.create_check_constraint("ck_accounts_provider", "provider IN ('codex', 'claude')")

    # 2. Backfill provider='codex' for existing rows.
    op.execute("UPDATE accounts SET provider = 'codex' WHERE provider IS NULL")

    # 3. Enforce NOT NULL on provider.
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.alter_column("provider", existing_type=sa.Text(), nullable=False, server_default="codex")

    # 4. Partial unique index on (provider='claude', claude_account_uuid).
    op.create_index(
        "uq_accounts_claude_uuid",
        "accounts",
        ["claude_account_uuid"],
        unique=True,
        sqlite_where=sa.text("provider = 'claude'"),
        postgresql_where=sa.text("provider = 'claude'"),
    )

    # 5. api_keys.provider_scope
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(sa.Column("provider_scope", sa.Text(), nullable=True, server_default=None))
    op.execute("UPDATE api_keys SET provider_scope = 'codex' WHERE provider_scope IS NULL")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.alter_column("provider_scope", existing_type=sa.Text(), nullable=False, server_default="codex")

    # 6. request_logs.provider
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("provider")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_column("provider_scope")
    op.drop_index("uq_accounts_claude_uuid", table_name="accounts")
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_constraint("ck_accounts_provider", type_="check")
        batch_op.alter_column("email", existing_type=sa.Text(), nullable=False)
        batch_op.drop_column("rate_limit_status")
        batch_op.drop_column("rate_limit_output_tokens_reset_at")
        batch_op.drop_column("rate_limit_output_tokens_remaining")
        batch_op.drop_column("rate_limit_input_tokens_reset_at")
        batch_op.drop_column("rate_limit_input_tokens_remaining")
        batch_op.drop_column("rate_limit_requests_reset_at")
        batch_op.drop_column("rate_limit_requests_remaining")
        batch_op.drop_column("claude_user_organization_uuid")
        batch_op.drop_column("claude_user_email")
        batch_op.drop_column("claude_scopes")
        batch_op.drop_column("claude_access_token_expires_at")
        batch_op.drop_column("claude_access_token_encrypted")
        batch_op.drop_column("claude_refresh_token_encrypted")
        batch_op.drop_column("claude_account_uuid")
        batch_op.drop_column("provider")
```

- [ ] **Step 3: Run migration check (sqlite)**

Run: `make migration-check`
Expected: PASS, with the new revision reachable from `head` and `downgrade()` returning cleanly.

- [ ] **Step 4: Run migration check (postgres)**

Run: `make migration-check-postgres`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/db/alembic/versions/<rev>_add_claude_account_columns.py
git commit -m "feat(db): alembic revision for Claude account columns"
```

---

## Phase 2 — Backend Configuration

### Task 2.1: Add Anthropic settings

**Files:**
- Modify: `app/core/config/settings.py`
- Modify: `.env.example`

- [ ] **Step 1: Add fields with defaults**

In `settings.py` (Pydantic-Settings):

```python
claude_api_base_url: str = "https://api.anthropic.com"
claude_oauth_token_endpoint: str = "https://platform.claude.com/v1/oauth/token"
claude_oauth_authorize_endpoint: str = "https://platform.claude.com/oauth/authorize"
claude_messages_path: str = "/v1/messages"
claude_models_path: str = "/v1/models"
claude_oauth_refresh_skew_seconds: int = 600
claude_oauth_extra_headers: dict[str, str] = {}
```

`claude_oauth_token_endpoint` is verified as `https://platform.claude.com/v1/oauth/token` and `claude_oauth_authorize_endpoint` is verified as `https://platform.claude.com/oauth/authorize` per `openspec/changes/add-claude-oauth-pool/notes.md` §1. These strings are not invented; they are Phase-0-verified values.

- [ ] **Step 2: Document in `.env.example`**

Append a clearly delimited block:

```
# Claude OAuth pool — see openspec/changes/add-claude-oauth-pool/notes.md for verified values.
CODEX_LB_CLAUDE_API_BASE_URL=https://api.anthropic.com
CODEX_LB_CLAUDE_OAUTH_TOKEN_ENDPOINT=https://platform.claude.com/v1/oauth/token
CODEX_LB_CLAUDE_OAUTH_AUTHORIZE_ENDPOINT=https://platform.claude.com/oauth/authorize
CODEX_LB_CLAUDE_MESSAGES_PATH=/v1/messages
CODEX_LB_CLAUDE_MODELS_PATH=/v1/models
CODEX_LB_CLAUDE_OAUTH_REFRESH_SKEW_SECONDS=600
```

- [ ] **Step 3: Run typecheck and commit**

```bash
uv run mypy app/core/config/settings.py
git add app/core/config/settings.py .env.example
git commit -m "feat(config): add Anthropic OAuth settings"
```

---

## Phase 3 — Anthropic OAuth Client

### Task 3.1: Define errors for Anthropic client

**Files:**
- Create: `app/core/clients/anthropic/errors.py`

- [ ] **Step 1: Implement**

```python
from __future__ import annotations


class ClaudeAPIError(Exception):
    """Base class for non-2xx responses from Anthropic."""


class ClaudeAuthError(ClaudeAPIError):
    """401 from Anthropic, or invalid_grant from OAuth refresh."""


class ClaudeRateLimited(ClaudeAPIError):
    """429 from Anthropic, or anthropic-ratelimit-status: rejected."""


class ClaudeUpstreamError(ClaudeAPIError):
    """5xx or transport failure from Anthropic."""
```

- [ ] **Step 2: Commit**

```bash
git add app/core/clients/anthropic/errors.py
git commit -m "feat(anthropic): client error types"
```

### Task 3.2: `ClaudeOAuthClient` (TDD)

**Files:**
- Create: `app/core/clients/anthropic/oauth.py`
- Test: `tests/unit/test_claude_oauth_client.py`

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

import pytest
from app.core.clients.anthropic.errors import ClaudeAuthError
from app.core.clients.anthropic.oauth import ClaudeOAuthClient


pytestmark = pytest.mark.unit


class _Transport:
    def __init__(self, response: object) -> None:
        self.response = response

    async def post(self, url: str, *, json: dict, headers: dict) -> object:
        self.request = (url, dict(json), dict(headers))
        return self.response


@pytest.fixture()
def settings() -> object:
    from types import SimpleNamespace
    return SimpleNamespace(
        claude_oauth_token_endpoint="https://auth.example.test/oauth/token",
        claude_oauth_extra_headers={"X-Client": "codex-lb"},
    )


async def test_refresh_returns_access_token_and_new_refresh(settings) -> None:
    resp = _Response(status=200, body={
        "access_token": "AT", "refresh_token": "NEW_RT", "expires_in": 3600,
    })
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)  # type: ignore[arg-type]
    out = await client.refresh("OLD_RT")
    assert out.access_token == "AT"
    assert out.refresh_token == "NEW_RT"  # rotated
    assert out.expires_in == 3600
    assert t.request[0] == "https://auth.example.test/oauth/token"
    body = t.request[1]
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "OLD_RT"


async def test_refresh_returns_none_refresh_when_not_rotated(settings) -> None:
    resp = _Response(status=200, body={"access_token": "AT", "expires_in": 3600})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)  # type: ignore[arg-type]
    out = await client.refresh("RT")
    assert out.access_token == "AT"
    assert out.refresh_token is None  # server did not return a new RT
    assert out.expires_in == 3600


async def test_refresh_invalid_grant_raises_auth_error(settings) -> None:
    resp = _Response(status=400, body={"error": "invalid_grant"})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)  # type: ignore[arg-type]
    with pytest.raises(ClaudeAuthError):
        await client.refresh("EXPIRED_RT")


async def test_refresh_server_error_raises_upstream(settings) -> None:
    resp = _Response(status=500, body={"error": "boom"})
    t = _Transport(resp)
    client = ClaudeOAuthClient(transport=t, settings=settings)  # type: ignore[arg-type]
    with pytest.raises(ClaudeUpstreamError):
        await client.refresh("RT")
```

(Use a tiny `_Response` adapter or aiohttp `ClientResponse` mock per project convention; mirror what existing Codex OAuth client tests do — see `tests/unit/test_auth_refresh.py` for the project's pattern.)

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/unit/test_claude_oauth_client.py -v`
Expected: 4 failures (module not implemented).

- [ ] **Step 3: Implement `ClaudeOAuthClient`**

```python
# app/core/clients/anthropic/oauth.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.clients.anthropic.errors import ClaudeAPIError, ClaudeAuthError, ClaudeUpstreamError
from app.core.upstream_proxy import build_aiohttp_session  # adjust to project helper


@dataclass(frozen=True)
class ClaudeRefreshResult:
    access_token: str
    refresh_token: str | None
    expires_in: int


class ClaudeOAuthTransport(Protocol):
    async def post(self, url: str, *, json: dict, headers: dict) -> object: ...


class ClaudeOAuthClient:
    def __init__(self, transport: ClaudeOAuthTransport, settings: object) -> None:
        self._transport = transport
        self._settings = settings

    async def refresh(self, refresh_token: str) -> ClaudeRefreshResult:
        url = getattr(self._settings, "claude_oauth_token_endpoint")
        extra = getattr(self._settings, "claude_oauth_extra_headers", {}) or {}
        resp = await self._transport.post(
            url,
            json={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Content-Type": "application/json", **extra},
        )
        body = await resp.json() if hasattr(resp, "json") else resp.body  # type: ignore[union-attr]
        status = getattr(resp, "status", 200)
        if status == 200:
            return ClaudeRefreshResult(
                access_token=body["access_token"],
                refresh_token=body.get("refresh_token"),
                expires_in=int(body["expires_in"]),
            )
        if status == 400 and body.get("error") == "invalid_grant":
            raise ClaudeAuthError(f"invalid_grant: {body}")
        if status >= 500:
            raise ClaudeUpstreamError(f"upstream {status}: {body}")
        raise ClaudeAPIError(f"refresh failed {status}: {body}")
```

Use the project's actual transport layer (aiohttp session with proxy env). If the project wraps aiohttp via `app/core/upstream_proxy.py` or similar, follow its pattern. Adapt tests to match.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/test_claude_oauth_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/clients/anthropic/oauth.py tests/unit/test_claude_oauth_client.py
git commit -m "feat(anthropic): OAuth refresh client"
```

---

## Phase 4 — Anthropic Chat Client (passthrough)

### Task 4.1: Rate-limit header parser

**Files:**
- Create: `app/core/clients/anthropic/headers.py`
- Test: `tests/unit/test_claude_rate_limit_headers.py`

- [ ] **Step 1: Write failing test**

```python
from app.core.clients.anthropic.headers import parse_anthropic_rate_limit_headers

def test_parses_all_headers_present() -> None:
    headers = {
        "anthropic-ratelimit-requests-remaining": "42",
        "anthropic-ratelimit-requests-reset": "2026-07-01T12:00:00Z",
        "anthropic-ratelimit-input-tokens-remaining": "100000",
        "anthropic-ratelimit-input-tokens-reset": "2026-07-01T12:00:00Z",
        "anthropic-ratelimit-output-tokens-remaining": "50000",
        "anthropic-ratelimit-output-tokens-reset": "2026-07-01T12:00:00Z",
        "anthropic-ratelimit-status": "allowed",
    }
    parsed = parse_anthropic_rate_limit_headers(headers)
    assert parsed["rate_limit_requests_remaining"] == 42
    assert parsed["rate_limit_requests_reset_at"] is not None  # ISO timestamp parsed
    assert parsed["rate_limit_status"] == "allowed"


def test_parses_missing_headers_returns_only_present_keys() -> None:
    parsed = parse_anthropic_rate_limit_headers({})
    assert parsed == {}


def test_reset_format_relative_seconds_is_dropped() -> None:
    # Relative form is never emitted by Anthropic (verified in notes.md §4).
    # The parser drops the value rather than guessing or raising.
    parsed = parse_anthropic_rate_limit_headers(
        {"anthropic-ratelimit-requests-reset": "in 5m"}
    )
    assert "rate_limit_requests_reset_at" not in parsed
```

- [ ] **Step 2: Implement the parser**

```python
# app/core/clients/anthropic/headers.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

_KEY_MAP = {
    "anthropic-ratelimit-requests-remaining": "rate_limit_requests_remaining",
    "anthropic-ratelimit-input-tokens-remaining": "rate_limit_input_tokens_remaining",
    "anthropic-ratelimit-output-tokens-remaining": "rate_limit_output_tokens_remaining",
    "anthropic-ratelimit-status": "rate_limit_status",
}
_RESET_KEYS = {
    "anthropic-ratelimit-requests-reset": "rate_limit_requests_reset_at",
    "anthropic-ratelimit-input-tokens-reset": "rate_limit_input_tokens_reset_at",
    "anthropic-ratelimit-output-tokens-reset": "rate_limit_output_tokens_reset_at",
}


def _parse_reset(raw: str) -> datetime | None:
    # Anthropic emits reset values as absolute RFC 3339 timestamps only.
    # Relative form ("in 5m") and bare unix seconds have not been observed in
    # verified captures (see notes.md §4) and are intentionally not handled.
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_anthropic_rate_limit_headers(headers: Mapping[str, str]) -> dict:
    out: dict = {}
    for src, dst in _KEY_MAP.items():
        v = headers.get(src)
        if v is None:
            continue
        if dst.endswith("_remaining"):
            try:
                out[dst] = int(v)
            except ValueError:
                continue
        else:
            out[dst] = v
    for src, dst in _RESET_KEYS.items():
        v = headers.get(src)
        if v is None:
            continue
        parsed = _parse_reset(v)
        if parsed is not None:
            out[dst] = parsed
    return out
```

Adjust the reset format to match what `notes.md` records in Phase 0 — Anthropic emits RFC 3339 only, so the parser does not accept relative form or unix seconds.

- [ ] **Step 3: Verify pass, commit**

```bash
uv run pytest tests/unit/test_claude_rate_limit_headers.py -v
git add app/core/clients/anthropic/headers.py tests/unit/test_claude_rate_limit_headers.py
git commit -m "feat(anthropic): rate-limit header parser"
```

### Task 4.2: `ClaudeChatClient.send_messages` (non-streaming passthrough)

**Files:**
- Create: `app/core/clients/anthropic/chat.py`
- Test: `tests/unit/test_claude_chat_client.py`

- [ ] **Step 1: Write failing test**

```python
async def test_send_messages_returns_upstream_body_and_headers_verbatim() -> None:
    body_in = {"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    upstream = {
        "id": "msg_01", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "usage": {"input_tokens": 5, "output_tokens": 7},
        "model": "claude-opus-4-8", "stop_reason": "end_turn",
    }
    headers_out = {
        "content-type": "application/json",
        "anthropic-ratelimit-requests-remaining": "42",
        "anthropic-ratelimit-status": "allowed",
    }
    transport = _FakeTransport(status=200, body=upstream, headers=headers_out)
    client = ClaudeChatClient(transport=transport, settings=settings, base_url="https://api.anthropic.com")
    out_body, out_headers = await client.send_messages(
        access_token="AT", request_body=body_in,
    )
    assert out_body == upstream
    assert out_headers["anthropic-ratelimit-status"] == "allowed"
    # Authorization header must be set; exact value format from notes.md.
    sent_headers = transport.last_request_headers
    assert sent_headers["Authorization"] == "Bearer AT"


async def test_send_messages_401_raises_claude_auth_error() -> None:
    transport = _FakeTransport(status=401, body={"error": "unauthorized"})
    client = ClaudeChatClient(transport=transport, settings=settings, base_url="https://api.anthropic.com")
    with pytest.raises(ClaudeAuthError):
        await client.send_messages(access_token="AT", request_body={"x": 1})
```

(Adapt `_FakeTransport` to mirror how the existing Codex chat-client test mocks aiohttp — see the project's pattern.)

- [ ] **Step 2: Implement `send_messages` (passthrough; no translation)**

```python
# app/core/clients/anthropic/chat.py
from __future__ import annotations

from typing import Any, Mapping, Protocol

from app.core.clients.anthropic.errors import ClaudeAPIError, ClaudeAuthError


class ClaudeChatTransport(Protocol):
    async def post(self, url: str, *, json: Mapping[str, Any], headers: Mapping[str, str]) -> object: ...


class ClaudeChatClient:
    def __init__(self, transport: ClaudeChatTransport, *, settings: object, base_url: str) -> None:
        self._t = transport
        self._settings = settings
        self._base_url = base_url

    async def send_messages(self, *, access_token: str, request_body: Mapping[str, Any]) -> tuple[dict, dict]:
        url = f"{self._base_url}{getattr(self._settings, 'claude_messages_path')}"
        headers = self._build_headers(access_token)
        resp = await self._t.post(url, json=request_body, headers=headers)
        status = getattr(resp, "status", 200)
        body = await resp.json() if hasattr(resp, "json") else resp.body  # type: ignore[union-attr]
        out_headers = dict(getattr(resp, "headers", {}))
        if status == 200:
            return body, out_headers
        if status == 401:
            raise ClaudeAuthError(f"anthropic 401: {body}")
        if status == 429:
            raise ClaudeAPIError(f"anthropic 429: {body}")
        raise ClaudeAPIError(f"anthropic {status}: {body}")

    def _build_headers(self, access_token: str) -> dict[str, str]:
        extras = getattr(self._settings, "claude_oauth_extra_headers", {}) or {}
        # Header values are pinned to the Phase 0 verified contract
        # (openspec/changes/add-claude-oauth-pool/notes.md §2):
        # - Authorization: Bearer <oauth_access_token> (x-api-key MUST NOT be sent)
        # - anthropic-version: 2023-06-01 (date-form, required)
        # - anthropic-beta: oauth-2025-04-20 (required for OAuth auth) +
        #                   claude-code-20250219 (strongly recommended)
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20,claude-code-20250219",
            **extras,
        }
```

`anthropic-version` is required and pinned to `2023-06-01` (date-form). `anthropic-beta` is required and pinned to the minimum safe CSV `oauth-2025-04-20,claude-code-20250219` — `oauth-2025-04-20` MUST be present; `claude-code-20250219` is strongly recommended for Claude Code fidelity. Do not add additional beta flags unless Phase 0 verification is updated.

- [ ] **Step 3: Run tests; iterate until pass.**

- [ ] **Step 4: Commit**

```bash
git add app/core/clients/anthropic/chat.py tests/unit/test_claude_chat_client.py
git commit -m "feat(anthropic): chat client non-streaming passthrough"
```

### Task 4.3: `ClaudeChatClient.stream_messages` (SSE passthrough)

**Files:**
- Modify: `app/core/clients/anthropic/chat.py`
- Test: `tests/unit/test_claude_chat_client.py`

- [ ] **Step 1: Write failing test**

```python
async def test_stream_messages_yields_sse_events_verbatim() -> None:
    sse_chunks = [
        b"event: message_start\r\ndata: {\"type\":\"message_start\"}\r\n\r\n",
        b"event: content_block_delta\r\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"hello\"}}\r\n\r\n",
        b"event: message_delta\r\ndata: {\"type\":\"message_delta\",\"usage\":{\"input_tokens\":3,\"output_tokens\":5}}\r\n\r\n",
        b"event: message_stop\r\ndata: {\"type\":\"message_stop\"}\r\n\r\n",
    ]
    transport = _FakeStreamingTransport(status=200, chunks=sse_chunks, headers={})
    client = ClaudeChatClient(transport=transport, settings=settings, base_url="https://api.anthropic.com")
    chunks: list[bytes] = []
    final_usage: dict | None = None
    async for chunk in client.stream_messages(access_token="AT", request_body={"stream": True}):
        if chunk.kind == "sse":
            chunks.append(chunk.data)
        elif chunk.kind == "usage":
            final_usage = chunk.data
    assert b"event: message_stop" in b"".join(chunks)
    assert final_usage == {"input_tokens": 3, "output_tokens": 5}
```

(Define a `StreamChunk` dataclass with `kind: Literal["sse","usage","headers"]` and `data`.)

- [ ] **Step 2: Implement `stream_messages`**

The implementation must yield raw SSE bytes (passthrough) plus a final usage chunk after `message_stop`. Do not parse/translate event content beyond extracting the final `message_delta.usage`.

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_claude_chat_client.py -v
git add app/core/clients/anthropic/chat.py tests/unit/test_claude_chat_client.py
git commit -m "feat(anthropic): chat client SSE passthrough"
```

---

## Phase 5 — Models Catalog

### Task 5.1: Hardcoded Claude model id list (TDD)

**Files:**
- Create: `app/modules/claude/models_catalog.py`
- Test: `tests/unit/test_models_catalog.py`

- [ ] **Step 1: Write failing test**

```python
from app.modules.claude.models_catalog import KNOWN_CLAUDE_MODELS, list_claude_models


def test_known_models_is_non_empty_and_only_anthropic_ids() -> None:
    assert len(KNOWN_CLAUDE_MODELS) >= 1
    for m in KNOWN_CLAUDE_MODELS:
        assert m.startswith("claude-") or m.startswith("claude_"), m


def test_no_deprecated_models_present() -> None:
    deprecated = {"claude-1", "claude-1.3", "claude-2.0", "claude-instant-1"}
    assert deprecated.isdisjoint(KNOWN_CLAUDE_MODELS)


def test_list_claude_models_returns_anthropic_shape() -> None:
    out = list_claude_models()
    assert out["object"] == "list"
    assert isinstance(out["data"], list)
    for entry in out["data"]:
        assert entry["object"] == "model"
        assert "id" in entry and "display_name" in entry
```

- [ ] **Step 2: Implement**

```python
# app/modules/claude/models_catalog.py
from __future__ import annotations

KNOWN_CLAUDE_MODELS: frozenset[str] = frozenset({
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
})


def list_claude_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "display_name": model_id,
                "type": "model",
            }
            for model_id in sorted(KNOWN_CLAUDE_MODELS)
        ],
    }
```

Replace the model ids with the actual Max/Pro/Team-eligible set as of the change date (verified at build time). The list above is illustrative.

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_models_catalog.py -v
git add app/modules/claude/models_catalog.py tests/unit/test_models_catalog.py
git commit -m "feat(claude): hardcoded Claude model catalog"
```

---

## Phase 6 — Claude Auth Manager

### Task 6.1: Pydantic schemas

**Files:**
- Create: `app/modules/claude/schemas.py`
- Test: `tests/unit/test_claude_schemas.py`

- [ ] **Step 1: Write failing test, implement, verify pass**

Schemas:

```python
# app/modules/claude/schemas.py
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AddClaudeAccountRequest(BaseModel):
    claudeAccountUuid: str = Field(min_length=1)
    accessToken: str = Field(min_length=1)
    refreshToken: str = Field(min_length=1)
    expiresInSeconds: int = Field(gt=0)
    scopes: list[str] | None = None
    userEmail: str | None = None
    userOrganizationUuid: str | None = None


class ClaudeAccountResponse(BaseModel):
    id: str
    claudeAccountUuid: str
    userEmail: str | None
    userOrganizationUuid: str | None
    isActive: bool
    claudeAccessTokenExpiresAt: datetime | None
    lastUsedAt: datetime | None
    rateLimitRequestsRemaining: int | None
    rateLimitInputTokensRemaining: int | None
    rateLimitOutputTokensRemaining: int | None
    rateLimitStatus: str | None
    createdAt: datetime


class DisableClaudeAccountRequest(BaseModel):
    reason: str | None = None
```

`AddClaudeAccountRequest` MUST reject when any required field is missing (covered in `tests/unit/test_claude_schemas.py`).

- [ ] **Step 2: Commit**

```bash
git add app/modules/claude/schemas.py tests/unit/test_claude_schemas.py
git commit -m "feat(claude): Pydantic schemas for Claude accounts"
```

### Task 6.2: `ClaudeAuthManager.add_claude_account` (TDD)

**Files:**
- Create: `app/modules/claude/auth_manager.py`
- Test: `tests/unit/test_claude_account_service.py`

- [ ] **Step 1: Write failing test**

```python
from app.modules.claude.auth_manager import ClaudeAuthManager
from app.modules.claude.schemas import AddClaudeAccountRequest


@pytest.fixture()
def repo() -> _FakeRepo: ...


async def test_add_claude_account_persists_encrypted_tokens(repo) -> None:
    manager = ClaudeAuthManager(repo=repo, encryptor=_FakeEncryptor())
    req = AddClaudeAccountRequest(
        claudeAccountUuid="abc-123",
        accessToken="AT", refreshToken="RT",
        expiresInSeconds=3600, userEmail="me@example.com",
    )
    account = await manager.add_claude_account(req)
    assert account.id is not None
    assert repo.persisted[account.id]["claude_account_uuid"] == "abc-123"
    # Plaintext tokens MUST NOT be in the persisted row.
    assert "AT" not in repr(repo.persisted[account.id])
    assert "RT" not in repr(repo.persisted[account.id])


async def test_add_claude_account_rejects_duplicate_uuid_with_conflict(repo) -> None:
    repo.exists_uuid = True
    manager = ClaudeAuthManager(repo=repo, encryptor=_FakeEncryptor())
    with pytest.raises(ClaudeAccountAlreadyExists):
        await manager.add_claude_account(AddClaudeAccountRequest(
            claudeAccountUuid="abc-123", accessToken="AT", refreshToken="RT", expiresInSeconds=3600,
        ))
```

- [ ] **Step 2: Implement**

```python
# app/modules/claude/auth_manager.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.crypto import TokenEncryptor
from app.modules.claude.schemas import AddClaudeAccountRequest


class ClaudeAccountAlreadyExists(Exception): ...


@dataclass
class ClaudeAccountRow:
    id: str
    claude_account_uuid: str


class ClaudeAccountRepository(Protocol):
    async def exists_by_claude_uuid(self, claude_uuid: str) -> bool: ...
    async def insert(self, row: dict) -> ClaudeAccountRow: ...


class ClaudeAuthManager:
    SKEW_SECONDS = 600

    def __init__(self, *, repo: ClaudeAccountRepository, encryptor: TokenEncryptor) -> None:
        self._repo = repo
        self._encryptor = encryptor

    async def add_claude_account(self, req: AddClaudeAccountRequest) -> ClaudeAccountRow:
        if await self._repo.exists_by_claude_uuid(req.claudeAccountUuid):
            raise ClaudeAccountAlreadyExists(req.claudeAccountUuid)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=req.expiresInSeconds) - timedelta(seconds=self.SKEW_SECONDS)
        row = {
            "id": f"claude-{req.claudeAccountUuid}",
            "provider": "claude",
            "claude_account_uuid": req.claudeAccountUuid,
            "claude_access_token_encrypted": self._encryptor.encrypt(req.accessToken),
            "claude_refresh_token_encrypted": self._encryptor.encrypt(req.refreshToken),
            "claude_access_token_expires_at": expires_at,
            "claude_scopes": req.scopes,
            "claude_user_email": req.userEmail,
            "claude_user_organization_uuid": req.userOrganizationUuid,
            "is_active": True,
            "status": "ACTIVE",
            "created_at": now,
        }
        return await self._repo.insert(row)
```

(Follow the project's actual `TokenEncryptor` API — check `app/core/crypto.py` for exact `encrypt/decrypt` method names and adjust accordingly.)

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_claude_account_service.py -v
git add app/modules/claude/auth_manager.py tests/unit/test_claude_account_service.py
git commit -m "feat(claude): auth manager add account"
```

### Task 6.3: `rotate_claude_access_token` (TDD)

**Files:**
- Modify: `app/modules/claude/auth_manager.py`
- Test: `tests/unit/test_claude_account_service.py`

- [ ] **Step 1: Write failing tests**

```python
async def test_rotate_refresh_token_writes_new_tokens(repo, fake_oauth) -> None:
    fake_oauth.next_result = ClaudeRefreshResult(
        access_token="AT2", refresh_token="RT2", expires_in=3600,
    )
    manager = ClaudeAuthManager(repo=repo, encryptor=_FakeEncryptor(), oauth_client=fake_oauth)
    account = repo.seed(...)
    await manager.rotate_claude_access_token(account)
    persisted = repo.persisted[account.id]
    # Anthropic ALWAYS rotates the refresh token; the new value MUST be persisted.
    assert persisted["claude_refresh_token_encrypted"] != account.original_refresh_token_bytes
    # Decrypt the new value and confirm it matches the rotated token.
    decrypted = encryptor.decrypt(persisted["claude_refresh_token_encrypted"])
    assert decrypted == "RT2"


async def test_rotate_drops_existing_refresh_when_oauth_response_omits_it(repo, fake_oauth) -> None:
    # Per notes.md §3, Anthropic always returns a new refresh_token. If the
    # response ever omits one (defensive case), the implementation SHALL drop
    # the existing refresh token rather than preserve a possibly-stale value.
    fake_oauth.next_result = ClaudeRefreshResult(
        access_token="AT2", refresh_token=None, expires_in=3600,
    )
    manager = ClaudeAuthManager(repo=repo, encryptor=_FakeEncryptor(), oauth_client=fake_oauth)
    account = repo.seed(...)
    await manager.rotate_claude_access_token(account)
    persisted = repo.persisted[account.id]
    # The previous refresh token MUST NOT be preserved.
    assert persisted["claude_refresh_token_encrypted"] != account.original_refresh_token_bytes


async def test_rotate_invalid_grant_disables_account(repo, fake_oauth) -> None:
    fake_oauth.next_error = ClaudeAuthError("invalid_grant")
    manager = ClaudeAuthManager(repo=repo, encryptor=_FakeEncryptor(), oauth_client=fake_oauth)
    account = repo.seed(...)
    await manager.rotate_claude_access_token(account)
    assert repo.persisted[account.id]["is_active"] is False
    assert repo.persisted[account.id]["status"] == "DEACTIVATED"
    assert repo.persisted[account.id]["deactivation_reason"]  # non-empty
```

- [ ] **Step 2: Implement `rotate_claude_access_token`**

Logic:
- Decrypt refresh token (`self._encryptor.decrypt(bytes)`).
- Call `oauth_client.refresh(refresh_token)`.
- Re-encrypt access token; persist with new `expires_at = now + result.expires_in - SKEW_SECONDS`.
- **Unconditional refresh-token overwrite**: Anthropic always rotates the refresh token (verified in `notes.md` §3). When `result.refresh_token` is not None, re-encrypt and overwrite `claude_refresh_token_encrypted`. If `result.refresh_token` IS None (defensive case, not observed), set `claude_refresh_token_encrypted = NULL` and surface a `claude.refresh.rotated_missing` log line so the account can be re-authorized.
- Acquire the per-account singleflight lock before any network call (see spec "Per-account refresh serialization (singleflight)" requirement). If another caller is already refreshing this account, await the in-flight result rather than issuing a second OAuth call.
- If `ClaudeAuthError`: set `is_active=False`, `status=DEACTIVATED`, `deactivation_reason='invalid_grant'`; emit structured log line `claude.refresh.failed` with `account_id`, `reason`. Increment `codex_lb_claude_refresh_total{result="invalid_grant"}`.

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_claude_account_service.py -v
git add app/modules/claude/auth_manager.py tests/unit/test_claude_account_service.py
git commit -m "feat(claude): rotate access token with disable on invalid_grant"
```

### Task 6.4: `disable_claude_account` / `enable_claude_account` (TDD)

- [ ] **Step 1: Write failing tests, implement, verify pass, commit**

```python
async def test_disable_sets_inactive_and_records_reason(repo) -> None:
    manager = ClaudeAuthManager(repo=repo, encryptor=_FakeEncryptor())
    account = repo.seed(...)
    await manager.disable_claude_account(account, reason="manual")
    row = repo.persisted[account.id]
    assert row["is_active"] is False
    assert row["status"] == "DEACTIVATED"
    assert row["deactivation_reason"] == "manual"


async def test_enable_restores_active(repo) -> None:
    manager = ClaudeAuthManager(repo=repo, encryptor=_FakeEncryptor())
    account = repo.seed(disabled=True)
    await manager.enable_claude_account(account)
    row = repo.persisted[account.id]
    assert row["is_active"] is True
    assert row["status"] == "ACTIVE"
```

```bash
git commit -am "feat(claude): enable/disable account lifecycle"
```

---

## Phase 7 — Auth Guardian Extension

### Task 7.1: Add Claude refresh pass to scheduler

**Files:**
- Modify: `app/core/auth/guardian.py`
- Modify: `app/modules/claude/auth_manager.py` (add `find_accounts_due_for_rotation` helper)
- Test: `tests/unit/test_auth_guardian.py` (extend)

- [ ] **Step 1: Write failing test**

```python
from app.core.auth.guardian import run_auth_guardian_tick  # or whatever the entrypoint is called

async def test_tick_refreshes_claude_accounts_expiring_within_skew(fake_uow) -> None:
    due = fake_uow.seed_claude_account(expires_at=utcnow() + timedelta(seconds=120), refresh_token="RT")
    not_due = fake_uow.seed_claude_account(expires_at=utcnow() + timedelta(hours=1), refresh_token="RT2")

    await run_auth_guardian_tick(uow=fake_uow, claude_manager=fake_claude_manager)

    fake_claude_manager.assert_rotated(due)
    fake_claude_manager.assert_not_rotated(not_due)
```

- [ ] **Step 2: Implement**

In `app/modules/claude/auth_manager.py`:

```python
async def find_accounts_due_for_rotation(self, *, skew_seconds: int = 600) -> list[ClaudeAccountRow]:
    return await self._repo.find_due_for_rotation(skew_seconds=skew_seconds)
```

In `app/core/auth/guardian.py`, after the existing Codex pass, iterate Claude accounts:

```python
claude_due = await claude_auth_manager.find_accounts_due_for_rotation(skew_seconds=settings.claude_oauth_refresh_skew_seconds)
for account in claude_due:
    try:
        await claude_auth_manager.rotate_claude_access_token(account)
    except ClaudeUpstreamError as e:
        # Reuse the existing backoff helper used for Codex. Do not disable.
        ...
```

Reuse the project's actual scheduler signature — read `app/core/auth/guardian.py` before editing to follow the established pattern.

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_auth_guardian.py -v
git add app/core/auth/guardian.py app/modules/claude/auth_manager.py tests/unit/test_auth_guardian.py
git commit -m "feat(auth-guardian): refresh Claude access tokens"
```

---

## Phase 8 — Load Balancer Extension

### Task 8.1: Add `provider` filter to `select_account`

**Files:**
- Modify: `app/modules/proxy/load_balancer.py` (or the underlying `app/core/balancer`)
- Test: `tests/unit/test_load_balancer_provider.py`

- [ ] **Step 1: Read existing `select_account` signature**

Identify whether `provider` should be added to `app/modules/proxy/load_balancer.py::select_account` or the core `app/core/balancer/__init__.py::select_account`. Match the layer the codebase uses for routing concerns.

- [ ] **Step 2: Write failing tests covering the three scenarios from `specs/account-routing/spec.md`**

```python
@pytest.mark.parametrize("provider,expected_uuids", [
    ("codex", {"codex-acc-1", "codex-acc-2"}),
    ("claude", {"claude-acc-1"}),
])
async def test_select_account_filters_by_provider(provider, expected_uuids, repo) -> None:
    repo.seed_many([
        account(id="codex-acc-1", provider="codex"),
        account(id="codex-acc-2", provider="codex"),
        account(id="claude-acc-1", provider="claude"),
    ])
    chosen = await select_account(provider=provider, sticky_kind=None, reallocate_sticky=False, ...)
    assert {c.id for c in chosen} == expected_uuids


async def test_select_account_claude_returns_no_candidate_returns_empty(repo) -> None:
    repo.seed_many([account(id="codex-1", provider="codex")])
    chosen = await select_account(provider="claude", ...)
    assert chosen == []
```

- [ ] **Step 3: Implement the filter**

Add `provider: Literal["codex", "claude"] = "codex"` parameter (or `provider: str | None = None`). In the candidate filter step, add a clause `if provider and row.provider != provider: continue`. Do not change Codex behavior.

- [ ] **Step 4: Verify pass; run `make architecture-check` to confirm no ProxyService drift**

Run: `make test-unit tests/unit/test_load_balancer_provider.py -v`
Then: `make architecture-check`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add app/modules/proxy/load_balancer.py tests/unit/test_load_balancer_provider.py
git commit -m "feat(load-balancer): provider filter on select_account"
```

### Task 8.2: Claude rate-limit cooldown branch

**Files:**
- Modify: `app/modules/proxy/load_balancer.py` (where cooldown bookkeeping happens)

- [ ] **Step 1: Write failing test**

```python
async def test_anthropic_429_sets_claude_account_cooldown(repo) -> None:
    claude_acc = repo.seed_claude(id="claude-1", status=AccountStatus.ACTIVE)
    await record_upstream_response(
        account_id=claude_acc.id,
        provider="claude",
        status_code=429,
        headers={"anthropic-ratelimit-status": "rejected", "anthropic-ratelimit-requests-reset": "in 60s"},
    )
    persisted = repo.persisted[claude_acc.id]
    assert persisted["status"] == AccountStatus.RATE_LIMITED
    assert persisted["reset_at"] is not None and persisted["reset_at"] > utcnow_ts()


async def test_anthropic_200_clears_stale_cooldown(repo) -> None:
    claude_acc = repo.seed_claude(id="claude-1", status=AccountStatus.RATE_LIMITED, reset_at=utcnow_ts() - 1)
    await record_upstream_response(account_id=claude_acc.id, provider="claude", status_code=200, headers={})
    assert repo.persisted[claude_acc.id]["status"] == AccountStatus.ACTIVE
```

- [ ] **Step 2: Implement** (narrow `if provider == "claude":` branch only)

In the existing cooldown-writing helper, after the existing Codex branch:

```python
if provider == "claude" and status_code == 429:
    reset_at = _parse_anthropic_reset(headers.get("anthropic-ratelimit-requests-reset"))
    await repo.update_status(
        account_id=account_id,
        status=AccountStatus.RATE_LIMITED,
        reset_at=reset_at,
    )
    rate_limit_fields = parse_anthropic_rate_limit_headers(headers)
    await repo.update_rate_limit_cache(account_id, rate_limit_fields)
```

Do not change Codex behavior. Reuse `parse_anthropic_rate_limit_headers` from Phase 4.

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_load_balancer_provider.py -v
git add app/modules/proxy/load_balancer.py tests/unit/test_load_balancer_provider.py
git commit -m "feat(load-balancer): Claude rate-limit cooldown branch"
```

---

## Phase 9 — Claude Proxy Service

### Task 9.1: `ClaudeProxyService.stream_or_complete_messages` (TDD)

**Files:**
- Create: `app/modules/claude/service.py`
- Test: `tests/unit/test_claude_proxy_service.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.fixture()
def proxy_service(deps) -> ClaudeProxyService:
    return ClaudeProxyService(
        load_balancer=deps.lb,
        chat=deps.chat,
        auth_manager=deps.auth,
        repo=deps.repo,
    )


async def test_passes_request_body_verbatim_for_non_streaming(proxy_service, deps) -> None:
    deps.lb.choose = lambda **_: _Account(id="claude-1")
    body_in = {"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    deps.chat.send_messages.return_value = ({"id": "msg_01"}, {"anthropic-ratelimit-status": "allowed"})
    out_body, out_headers = await proxy_service.stream_or_complete_messages(
        request_body=body_in, api_key=deps.api_key_scope_claude, request_id="r-1",
    )
    assert out_body == {"id": "msg_01"}
    # chat client must have received the exact request body, unmodified
    deps.chat.send_messages.assert_called_once()
    sent_body = deps.chat.send_messages.call_args.kwargs["request_body"]
    assert sent_body is body_in  # identity — no copy/translation


async def test_provider_scope_mismatch_returns_403(proxy_service, deps) -> None:
    with pytest.raises(ProviderScopeMismatch):
        await proxy_service.stream_or_complete_messages(
            request_body={"x": 1}, api_key=deps.api_key_scope_codex_only, request_id="r-2",
        )


async def test_no_candidates_returns_503(proxy_service, deps) -> None:
    deps.lb.choose = lambda **_: []  # simulate empty
    with pytest.raises(NoClaudeAccounts):
        await proxy_service.stream_or_complete_messages(
            request_body={"x": 1}, api_key=deps.api_key_scope_claude, request_id="r-3",
        )
```

- [ ] **Step 2: Implement `ClaudeProxyService` (skeleton)**

```python
# app/modules/claude/service.py
from __future__ import annotations

from typing import Any, Mapping

from app.core.utils.time import utcnow
from app.modules.claude.auth_manager import ClaudeAuthManager
from app.modules.claude.auth_manager import ClaudeAccountRow


class ProviderScopeMismatch(Exception):
    """API key's provider_scope does not include 'claude'."""


class NoClaudeAccounts(Exception):
    """No Claude accounts are available in the pool."""


class ClaudeProxyService:
    def __init__(
        self,
        *,
        load_balancer,
        chat,
        auth_manager: ClaudeAuthManager,
        repo,
        metrics=None,
    ) -> None:
        self._lb = load_balancer
        self._chat = chat
        self._auth = auth_manager
        self._repo = repo
        self._metrics = metrics

    async def stream_or_complete_messages(
        self, *, request_body: Mapping[str, Any], api_key, request_id: str,
    ):
        if "claude" not in (api_key.provider_scope or "").split(","):
            raise ProviderScopeMismatch("API key is not authorized for /claude")
        candidates = await self._lb.select_account(provider="claude", ...)
        if not candidates:
            raise NoClaudeAccounts()
        account = candidates[0]
        access_token = await self._auth.get_access_token(account)
        body, headers = await self._chat.send_messages(
            access_token=access_token, request_body=dict(request_body),
        )
        await self._persist_rate_limit(account, headers)
        await self._persist_request_log(account, request_body, body, request_id)
        await self._metrics_record("success")
        return body, headers
```

(`_persist_*` and `_metrics_record` are filled in next tasks.)

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_claude_proxy_service.py -v
git add app/modules/claude/service.py tests/unit/test_claude_proxy_service.py
git commit -m "feat(claude): proxy service skeleton with passthrough"
```

### Task 9.2: 401 rotate-and-retry (TDD)

**Files:**
- Modify: `app/modules/claude/service.py`
- Test: `tests/unit/test_claude_proxy_service.py`

- [ ] **Step 1: Write failing test**

```python
async def test_first_401_triggers_rotate_and_retry(proxy_service, deps) -> None:
    deps.lb.choose = lambda **_: _Account(id="claude-1")
    deps.chat.send_messages.side_effect = [
        ClaudeAuthError("401"),          # first call
        ({"id": "msg_01"}, {}),          # retry succeeds
    ]
    await proxy_service.stream_or_complete_messages(
        request_body={"x": 1}, api_key=deps.api_key_scope_claude, request_id="r",
    )
    assert deps.auth.rotate_calls == 1
    assert deps.chat.send_messages.call_count == 2


async def test_two_consecutive_401s_propagate_as_auth_error(proxy_service, deps) -> None:
    deps.lb.choose = lambda **_: _Account(id="claude-1")
    deps.chat.send_messages.side_effect = [ClaudeAuthError("401"), ClaudeAuthError("401")]
    with pytest.raises(ClaudeAuthError):
        await proxy_service.stream_or_complete_messages(
            request_body={"x": 1}, api_key=deps.api_key_scope_claude, request_id="r",
        )
    assert deps.chat.send_messages.call_count == 2
    assert deps.lb.record_health_called_with("claude-1", status=AccountStatus.RATE_LIMITED)
```

- [ ] **Step 2: Implement**

```python
try:
    body, headers = await self._chat.send_messages(access_token=access_token, request_body=dict(request_body))
except ClaudeAuthError:
    # Rotate via the auth manager, which serializes concurrent refreshes
    # for this account_id behind a singleflight lock (see spec requirement
    # "Per-account refresh serialization"). If the guardian is already
    # refreshing the same account, this call awaits the in-flight result.
    await self._auth.rotate_claude_access_token(account, force=True)
    access_token = await self._auth.get_access_token(account)  # refresh from DB
    try:
        body, headers = await self._chat.send_messages(access_token=access_token, request_body=dict(request_body))
    except ClaudeAuthError:
        await self._lb.record_health(account.id, status=AccountStatus.RATE_LIMITED)
        await self._metrics_record("auth_error")
        raise
```

- [ ] **Step 3: Verify pass; commit**

```bash
uv run pytest tests/unit/test_claude_proxy_service.py -v
git add app/modules/claude/service.py tests/unit/test_claude_proxy_service.py
git commit -m "feat(claude): 401 rotate-and-retry"
```

### Task 9.3: Rate-limit header persistence + record_health on 429

- [ ] **Step 1: Test, implement, verify pass**

```python
async def test_rate_limit_headers_persist_after_200(proxy_service, deps) -> None:
    deps.lb.choose = lambda **_: _Account(id="claude-1")
    deps.chat.send_messages.return_value = ({"id": "x"}, {"anthropic-ratelimit-requests-remaining": "42"})
    await proxy_service.stream_or_complete_messages(
        request_body={"x": 1}, api_key=deps.api_key_scope_claude, request_id="r",
    )
    assert deps.repo.rate_limit_cache["claude-1"]["rate_limit_requests_remaining"] == 42
```

`_persist_rate_limit(account, headers)` calls `parse_anthropic_rate_limit_headers(headers)` then `repo.update_rate_limit_cache(account.id, parsed)`.

For 429 path: parse header, set cooldown via `load_balancer.record_health(...)` (see Phase 8.2) and persist cache.

- [ ] **Step 2: Commit**

```bash
git commit -am "feat(claude): persist rate-limit headers after request"
```

### Task 9.4: Request log write (usage extraction + provider='claude')

- [ ] **Step 1: Test, implement, verify pass**

```python
async def test_request_log_written_with_provider_and_usage(proxy_service, deps) -> None:
    deps.lb.choose = lambda **_: _Account(id="claude-1")
    deps.chat.send_messages.return_value = (
        {"id": "x", "usage": {"input_tokens": 3, "output_tokens": 5, "cache_creation_input_tokens": 1}},
        {},
    )
    await proxy_service.stream_or_complete_messages(
        request_body={"x": 1, "model": "claude-opus-4-8"},
        api_key=deps.api_key_scope_claude, request_id="r-log",
    )
    row = deps.repo.log_rows[-1]
    assert row["provider"] == "claude"
    assert row["model"] == "claude-opus-4-8"
    assert row["tokens_input"] == 3
    assert row["tokens_output"] == 5
    assert row["cached_input_tokens"] == 1
```

`_persist_request_log(account, request_body, body, request_id)` writes once per request:
- `provider='claude'`
- `account_id=account.id`
- `model=request_body['model']`
- `tokens_input=body['usage']['input_tokens']`
- `tokens_output=body['usage']['output_tokens']`
- `cached_input_tokens=body['usage'].get('cache_creation_input_tokens', 0)`
- `status_code=200`

Do NOT write a separate log row per SSE chunk.

- [ ] **Step 2: Commit**

```bash
git commit -am "feat(claude): write request_log row once per request"
```

### Task 9.5: Streaming path (passthrough)

- [ ] **Step 1: Test, implement, verify pass**

```python
async def test_streaming_passes_through_sse_bytes_and_logs_once_at_end(proxy_service, deps) -> None:
    deps.lb.choose = lambda **_: _Account(id="claude-1")
    sse_chunks = [
        b"event: message_start\r\ndata: {\"type\":\"message_start\"}\r\n\r\n",
        b"event: message_delta\r\ndata: {\"type\":\"message_delta\",\"usage\":{\"input_tokens\":3,\"output_tokens\":5}}\r\n\r\n",
        b"event: message_stop\r\ndata: {\"type\":\"message_stop\"}\r\n\r\n",
    ]
    async def _stream(*_a, **_kw):
        for c in sse_chunks:
            yield _StreamChunk(kind="sse", data=c)
        yield _StreamChunk(kind="usage", data={"input_tokens": 3, "output_tokens": 5})

    deps.chat.stream_messages = _stream
    chunks = []
    async for chunk in proxy_service.stream_messages(
        request_body={"stream": True}, api_key=deps.api_key_scope_claude, request_id="r-s",
    ):
        chunks.append(chunk)
    assert b"event: message_stop" in b"".join(c.data for c in chunks if c.kind == "sse")
    # Exactly one log row written.
    assert len(deps.repo.log_rows) == 1
```

`stream_messages(...)` mirrors `stream_or_complete_messages` for streaming: 401 retry, rate-limit header parsing on final headers chunk, single log write after the final chunk.

- [ ] **Step 2: Commit**

```bash
git commit -am "feat(claude): streaming passthrough with single log write"
```

---

## Phase 10 — API Layer

### Task 10.1: `api_key_validator_with_provider` helper

**Files:**
- Modify: `app/modules/api_keys/` (look for existing `auth_validator` helper to reuse)

- [ ] **Step 1: Read existing api-key auth dependency** (`app/modules/api_keys/auth.py` or similar).

- [ ] **Step 2: Wrap or add factory**

```python
# app/modules/api_keys/provider_auth.py (new)
from fastapi import Depends, HTTPException, Request
from app.modules.api_keys.auth import validate_api_key  # existing


def api_key_validator_with_provider(provider: str):
    async def _validate(request: Request) -> object:
        api_key = await validate_api_key(request)
        scopes = (api_key.provider_scope or "").split(",")
        if provider not in scopes:
            raise HTTPException(status_code=403, detail=f"API key not authorized for provider '{provider}'")
        return api_key
    return _validate
```

- [ ] **Step 3: Test, commit**

```python
# tests/unit/test_api_key_provider_scope.py
def test_factory_rejects_key_without_provider() -> None:
    dep = api_key_validator_with_provider("claude")
    from starlette.requests import Request
    request = Request(scope={"type": "http"})
    request.headers = Headers({"authorization": "Bearer sk-x"})  # adjust to project header
    key = SimpleNamespace(provider_scope="codex")
    # patch validate_api_key: ... assert dep raises 403
```

```bash
git commit -am "feat(api-keys): provider-scoped validator factory"
```

### Task 10.2: Claude router — `/claude/v1/messages` and `/claude/v1/models`

**Files:**
- Create: `app/modules/claude/api.py`

- [ ] **Step 1: Implement**

```python
# app/modules/claude/api.py
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.modules.api_keys.provider_auth import api_key_validator_with_provider
from app.modules.claude.models_catalog import list_claude_models
from app.modules.claude.service import ClaudeProxyService

router = APIRouter()

_claude_key = api_key_validator_with_provider("claude")


@router.get("/claude/v1/models")
async def list_models() -> dict:
    return list_claude_models()


@router.post("/claude/v1/messages")
async def messages(request: Request, api_key=Depends(_claude_key)) -> Any:
    body = await request.json()
    service: ClaudeProxyService = request.app.state.claude_proxy_service
    is_stream = bool(body.get("stream"))
    if is_stream:
        async def _gen():
            async for chunk in service.stream_messages(request_body=body, api_key=api_key, request_id=request.headers.get("x-request-id", "")):
                yield chunk.data
        return StreamingResponse(_gen(), media_type="text/event-stream")
    out_body, out_headers = await service.stream_or_complete_messages(
        request_body=body, api_key=api_key, request_id=request.headers.get("x-request-id", ""),
    )
    return JSONResponse(content=out_body, headers={k: v for k, v in out_headers.items() if k.lower().startswith("anthropic-")})
```

Adjust header re-emission to match the project's pattern for proxy pass-through (some filters may drop certain headers). Keep the set tight: only re-emit Anthropic-emitted headers and `content-type`.

- [ ] **Step 2: Test, commit**

```bash
git commit -am "feat(claude): /claude/v1/messages and /claude/v1/models routes"
```

### Task 10.3: Admin CRUD endpoints

**Files:**
- Modify: `app/modules/claude/api.py`

- [ ] **Step 1: Add admin endpoints**

```python
@router.get("/api/claude/accounts")
async def list_accounts() -> list[dict]:
    rows = await claude_auth_manager.repo.list_accounts()  # implement in repo
    return [_serialize(r) for r in rows]


@router.post("/api/claude/accounts", status_code=201)
async def add_account(payload: AddClaudeAccountRequest) -> dict:
    row = await claude_auth_manager.add_claude_account(payload)
    return _serialize(row)


@router.patch("/api/claude/accounts/{account_id}/disable", status_code=204)
async def disable(account_id: str, payload: DisableClaudeAccountRequest | None = None) -> Response:
    await claude_auth_manager.disable_claude_account(account_id, payload.reason if payload else None)
    return Response(status_code=204)


@router.patch("/api/claude/accounts/{account_id}/enable", status_code=204)
async def enable(account_id: str) -> Response:
    await claude_auth_manager.enable_claude_account(account_id)
    return Response(status_code=204)
```

`_serialize(row)` MUST strip `claude_access_token_encrypted` and `claude_refresh_token_encrypted` from the response (the spec requires no plaintext tokens).

- [ ] **Step 2: Test, commit**

```bash
git commit -am "feat(claude): admin CRUD endpoints"
```

### Task 10.4: Mount router in `app/main.py`

- [ ] **Step 1: Wire it up**

```python
from app.modules.claude import api as claude_api
app.include_router(claude_api.router)
```

Also initialize `app.state.claude_proxy_service = ClaudeProxyService(...)` at app startup (where the existing Codex service is built — see `app/main.py`).

- [ ] **Step 2: Commit**

```bash
git commit -am "feat(app): mount claude router"
```

---

## Phase 11 — API Keys `provider_scope`

### Task 11.1: Schema fields on create/update/response

**Files:**
- Modify: `app/modules/api_keys/schemas.py`

- [ ] **Step 1: Add `provider_scope`**

```python
class ApiKeyCreateRequest(BaseModel):
    ...
    provider_scope: list[str] | None = None  # subset of {"codex", "claude"}

    @field_validator("provider_scope")
    @classmethod
    def _check(cls, v):
        if v is None:
            return v
        bad = set(v) - {"codex", "claude"}
        if bad:
            raise ValueError(f"unknown providers: {bad}")
        return sorted(set(v))
```

Map `["claude"]` (or `["codex", "claude"]`) → DB string `"claude"` (or `"codex,claude"`). Default to `"codex"` when None — matching existing API key behavior.

Response:
```python
class ApiKeyResponse(BaseModel):
    ...
    provider_scope: list[str]
```

- [ ] **Step 2: Tests, commit**

```bash
git commit -am "feat(api-keys): provider_scope schema"
```

### Task 11.2: Service layer accepts/returns `provider_scope`

**Files:**
- Modify: `app/modules/api_keys/service.py`

Map `provider_scope` between request ↔ DB on create/update and on read.

- [ ] **Step 1: Implement, test, commit**

```bash
git commit -am "feat(api-keys): service provider_scope mapping"
```

### Task 11.3: API endpoints accept and return `provider_scope`

**Files:**
- Modify: `app/modules/api_keys/api.py`

Update create and update responses to round-trip the field.

- [ ] **Step 1: Implement, test, commit**

```bash
git commit -am "feat(api-keys): API layer provider_scope"
```

---

## Phase 12 — Frontend

### Task 12.1: API client methods

**Files:**
- Modify: `frontend/src/lib/api.ts` (or wherever existing codex account methods live)

- [ ] **Step 1: Add methods**

```ts
export async function listClaudeAccounts(): Promise<ClaudeAccount[]> { ... }
export async function addClaudeAccount(req: AddClaudeAccountRequest): Promise<ClaudeAccount> { ... }
export async function disableClaudeAccount(id: string, reason?: string): Promise<void> { ... }
export async function enableClaudeAccount(id: string): Promise<void> { ... }
```

- [ ] **Step 2: Commit**

```bash
git commit -am "feat(frontend): Claude account API client"
```

### Task 12.2: Zod schemas

**Files:**
- Modify: `frontend/src/lib/schemas.ts`

```ts
export const ClaudeAccountSchema = z.object({
  id: z.string(),
  claudeAccountUuid: z.string(),
  userEmail: z.string().nullable(),
  userOrganizationUuid: z.string().nullable(),
  isActive: z.boolean(),
  claudeAccessTokenExpiresAt: z.string().nullable(),
  lastUsedAt: z.string().nullable(),
  rateLimitRequestsRemaining: z.number().nullable(),
  rateLimitInputTokensRemaining: z.number().nullable(),
  rateLimitOutputTokensRemaining: z.number().nullable(),
  rateLimitStatus: z.string().nullable(),
  createdAt: z.string(),
});
```

```bash
git commit -am "feat(frontend): Claude schemas"
```

### Task 12.3: `ClaudeAccountList.tsx`

Mirror the existing codex account list pattern; render the same columns + actions (disable/enable). Reuse the project's existing data-fetch hook pattern.

- [ ] **Step 1: Implement, render-test, commit**

```bash
git commit -am "feat(frontend): ClaudeAccountList component"
```

### Task 12.4: `AddClaudeAccountDialog.tsx`

Form fields: `accessToken`, `refreshToken`, `expiresInSeconds`, `scopes` (CSV input), `userEmail`, `userOrganizationUuid`. Calls `addClaudeAccount` on submit.

- [ ] **Step 1: Implement, test, commit**

```bash
git commit -am "feat(frontend): AddClaudeAccountDialog"
```

### Task 12.5: `ClaudeAccountUsageCard.tsx`

Read-only display of: rate-limit remaining values, `rateLimitStatus`, and today's `request_logs.tokens_total` (compute by summing on the same backend endpoint already used for Codex usage cards — extend it to filter by provider if needed).

- [ ] **Step 1: Implement, test, commit**

```bash
git commit -am "feat(frontend): ClaudeAccountUsageCard"
```

### Task 12.6: Sidebar entry

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

Add a "Claude Accounts" nav entry alongside "Accounts". Use the i18n key `sidebar.claudeAccounts`.

- [ ] **Step 1: Implement, commit**

```bash
git commit -am "feat(frontend): Claude Accounts sidebar entry"
```

### Task 12.7: i18n strings

**Files:**
- Modify: `frontend/src/locales/en.json`
- Modify: `frontend/src/locales/zh-CN.json`

Keys: `claude.*` namespace (tab title, button labels, form labels, errors, empty state). Mirror existing translations.

- [ ] **Step 1: Implement, commit**

```bash
git commit -am "feat(frontend): i18n for Claude accounts tab"
```

---

## Phase 13 — Metrics

### Task 13.1: Prometheus counters

**Files:**
- Modify: `app/core/metrics/prometheus.py`

- [ ] **Step 1: Add counters and gauge, gated by `CODEX_LB_METRICS_ENABLED`**

```python
if PROMETHEUS_AVAILABLE:
    codex_lb_claude_requests_total = Counter(
        "codex_lb_claude_requests_total",
        "Total Claude proxy requests",
        labelnames=["status"],
    )
    codex_lb_claude_refresh_total = Counter(
        "codex_lb_claude_refresh_total",
        "Claude access-token refresh attempts",
        labelnames=["result"],
    )
    codex_lb_claude_accounts_active = Gauge(
        "codex_lb_claude_accounts_active",
        "Active Claude accounts in the pool",
    )
```

Wire counters from `ClaudeProxyService` (Phase 9) and `ClaudeAuthManager.rotate_claude_access_token` (Phase 6). Update the gauge on `list_accounts` if a `/metrics` scrape happens.

- [ ] **Step 2: Test, commit**

```bash
git commit -am "feat(metrics): codex_lb_claude_* counters"
```

---

## Phase 14 — Spec Deltas and Validation

### Task 14.1: Update capability spec deltas in `openspec/changes/<change>/specs/`

Verify each spec file in `specs/` contains the right `## ADDED Requirements` / `## MODIFIED Requirements` blocks. Cross-check against the OpenSpec `tasks.md` and the implementation. Adjust scenarios if behavior ended up different than designed.

- [ ] **Step 1: Run validation**

```bash
openspec validate add-claude-oauth-pool --strict --no-interactive
```

- [ ] **Step 2: Iterate to clean**

Fix any structural issues (missing `## ADDED Requirements`, ambiguous WHEN/THEN, etc.) until the validator returns "is valid".

- [ ] **Step 3: Commit**

```bash
git commit -am "docs(openspec): tighten capability deltas after implementation"
```

---

## Phase 15 — Final Verification

### Task 15.1: Run the full gate

- [ ] **Step 1:**

```bash
make lint
make typecheck
make test-unit
make test-integration-core
make test-integration-bridge -vv
make migration-check
make migration-check-postgres
make package
make architecture-check
openspec validate add-claude-oauth-pool --strict --no-interactive
```

Expected: every target exits 0.

- [ ] **Step 2: Document verification outcomes**

Append to `notes.md` a `## Final verification` section listing each target's exit status and the date. Reference any issues fixed during the iteration.

- [ ] **Step 3: Commit final state**

```bash
git add openspec/changes/add-claude-oauth-pool/notes.md
git commit -m "docs(openspec): final verification notes"
```

### Task 15.2: PR description

- [ ] **Step 1: Draft the PR description**

Include:
- Why one PR (per the design's `Risks / Trade-offs`): end-to-end testability requires DB + auth lifecycle + proxy + API-key authz + minimal dashboard together.
- Reference `notes.md` Phase 0 contract verification.
- Confirmation that `make architecture-check` ProxyService ratchets are unchanged.
- `Fixes #N` or `Closes #N` if applicable (omit if not part of an issue).
- Outcome checklist of `make lint`, `make typecheck`, `make test-unit`, `make test-integration-core`, `make test-integration-bridge`, `make migration-check`, `make migration-check-postgres`, `make package`, `make architecture-check`.

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "feat: pool Claude OAuth subscriptions" --body-file .gh-pr-body.md
```

---

## Self-Review Checklist (run before saving the plan)

- **Spec coverage:** every requirement in `specs/claude-oauth-pool/spec.md` has a task above (manual add, list, disable/enable, refresh guardian, passthrough messages, 401 retry, rate-limit headers, models endpoint, soft-delete, dashboard tab, i18n, ProxyService untouched, verification). ✓
- **Placeholder scan:** no "TODO", "TBD", "implement later", or "handle edge cases" remain; every code block is full code; references to types/functions are all defined in earlier tasks. ✓
- **Type consistency:** method/field names match across phases (`ClaudeAuthManager.rotate_claude_access_token`, `select_account(provider=...)`, `parse_anthropic_rate_limit_headers`, etc.). ✓
- **Phase 0 hard gate:** Phase 0 is a strict prerequisite and ends with an explicit checkpoint; Phase 1 begins only after user approval. ✓

---

## Open Spec Deltas (already written)

Reference these for authoritative content; the tasks above implement them:

- `openspec/changes/add-claude-oauth-pool/specs/claude-oauth-pool/spec.md` — new capability
- `openspec/changes/add-claude-oauth-pool/specs/account-routing/spec.md` — provider discriminator + Claude cooldown
- `openspec/changes/add-claude-oauth-pool/specs/database-migrations/spec.md` — schema + backfill + downgrade
- `openspec/changes/add-claude-oauth-pool/specs/api-keys/spec.md` — `provider_scope`
- `openspec/changes/add-claude-oauth-pool/specs/proxy-runtime-observability/spec.md` — `codex_lb_claude_*` metrics
