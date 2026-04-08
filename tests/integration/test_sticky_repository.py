from __future__ import annotations

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, OpenAIPlatformIdentity, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.upstream_identities.types import CHATGPT_WEB_PROVIDER_KIND, OPENAI_PLATFORM_PROVIDER_KIND

pytestmark = pytest.mark.integration


async def _create_chatgpt_account() -> Account:
    encryptor = TokenEncryptor()
    account = Account(
        id="sticky-repo-chatgpt",
        chatgpt_account_id="sticky-repo-chatgpt",
        email="sticky-repo@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
    return account


async def _create_platform_identity() -> OpenAIPlatformIdentity:
    encryptor = TokenEncryptor()
    identity = OpenAIPlatformIdentity(
        id="sticky-repo-platform",
        label="Sticky Platform",
        api_key_encrypted=encryptor.encrypt("sk-test"),
        organization_id=None,
        project_id=None,
        eligible_route_families="public_responses_http",
        status=AccountStatus.ACTIVE,
        last_validated_at=utcnow(),
        last_auth_failure_reason=None,
        deactivation_reason=None,
    )
    async with SessionLocal() as session:
        session.add(identity)
        await session.commit()
        await session.refresh(identity)
    return identity


@pytest.mark.asyncio
async def test_sticky_repository_isolates_identical_keys_by_provider(db_setup):
    account = await _create_chatgpt_account()
    identity = await _create_platform_identity()

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        await repo.upsert("shared-key", account.id, kind=StickySessionKind.PROMPT_CACHE)
        await repo.upsert_target(
            "shared-key",
            kind=StickySessionKind.PROMPT_CACHE,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=identity.id,
        )

        chatgpt_target = await repo.get_target(
            "shared-key",
            kind=StickySessionKind.PROMPT_CACHE,
            provider_kind=CHATGPT_WEB_PROVIDER_KIND,
        )
        platform_target = await repo.get_target(
            "shared-key",
            kind=StickySessionKind.PROMPT_CACHE,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
        )

        assert chatgpt_target is not None
        assert chatgpt_target.provider_kind == CHATGPT_WEB_PROVIDER_KIND
        assert chatgpt_target.routing_subject_id == account.id
        assert chatgpt_target.account_id == account.id

        assert platform_target is not None
        assert platform_target.provider_kind == OPENAI_PLATFORM_PROVIDER_KIND
        assert platform_target.routing_subject_id == identity.id
        assert platform_target.account_id is None

        rows = await repo.list_entries(sort_by="key", sort_dir="asc")
        assert [(row.sticky_session.provider_kind, row.display_name) for row in rows] == [
            (CHATGPT_WEB_PROVIDER_KIND, account.email),
            (OPENAI_PLATFORM_PROVIDER_KIND, identity.label),
        ]


@pytest.mark.asyncio
async def test_sticky_repository_default_delete_only_removes_chatgpt_scope(db_setup):
    account = await _create_chatgpt_account()
    identity = await _create_platform_identity()

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        await repo.upsert("shared-key", account.id, kind=StickySessionKind.PROMPT_CACHE)
        await repo.upsert_target(
            "shared-key",
            kind=StickySessionKind.PROMPT_CACHE,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
            routing_subject_id=identity.id,
        )

        deleted = await repo.delete("shared-key", kind=StickySessionKind.PROMPT_CACHE)
        assert deleted is True
        assert await repo.get_account_id("shared-key", kind=StickySessionKind.PROMPT_CACHE) is None

        platform_target = await repo.get_target(
            "shared-key",
            kind=StickySessionKind.PROMPT_CACHE,
            provider_kind=OPENAI_PLATFORM_PROVIDER_KIND,
        )
        assert platform_target is not None
        assert platform_target.routing_subject_id == identity.id
