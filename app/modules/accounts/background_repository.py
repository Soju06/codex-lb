from __future__ import annotations

from datetime import datetime

from app.db.models import Account, AccountStatus
from app.db.session import detach_session_objects, get_background_session
from app.modules.accounts.repository import AccountsRepository

_UNSET = object()


class BackgroundAccountsRepository:
    async def get_by_id(self, account_id: str) -> Account | None:
        async with get_background_session() as session:
            account = await AccountsRepository(session).get_by_id(account_id)
            detach_session_objects(session)
            return account

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        async with get_background_session() as session:
            accounts = await AccountsRepository(session).list_accounts(refresh_existing=refresh_existing)
            detach_session_objects(session)
            return accounts

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
    ) -> bool:
        async with get_background_session() as session:
            repo = AccountsRepository(session)
            if blocked_at is _UNSET:
                return await repo.update_status(account_id, status, deactivation_reason, reset_at)
            return await repo.update_status(account_id, status, deactivation_reason, reset_at, blocked_at)

    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None | object = _UNSET,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None | object = _UNSET,
    ) -> bool:
        async with get_background_session() as session:
            repo = AccountsRepository(session)
            kwargs = {
                "expected_status": expected_status,
                "expected_deactivation_reason": expected_deactivation_reason,
                "expected_reset_at": expected_reset_at,
            }
            if expected_blocked_at is not _UNSET:
                kwargs["expected_blocked_at"] = expected_blocked_at
            if blocked_at is _UNSET:
                return await repo.update_status_if_current(
                    account_id,
                    status,
                    deactivation_reason,
                    reset_at,
                    **kwargs,
                )
            return await repo.update_status_if_current(
                account_id,
                status,
                deactivation_reason,
                reset_at,
                blocked_at,
                **kwargs,
            )

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        async with get_background_session() as session:
            return await AccountsRepository(session).update_tokens(
                account_id,
                access_token_encrypted=access_token_encrypted,
                refresh_token_encrypted=refresh_token_encrypted,
                id_token_encrypted=id_token_encrypted,
                last_refresh=last_refresh,
                plan_type=plan_type,
                email=email,
                chatgpt_account_id=chatgpt_account_id,
                chatgpt_user_id=chatgpt_user_id,
                workspace_id=workspace_id,
                workspace_label=workspace_label,
                seat_type=seat_type,
            )

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool:
        async with get_background_session() as session:
            return await AccountsRepository(session).workspace_slot_taken(
                account_id=account_id,
                email=email,
                chatgpt_account_id=chatgpt_account_id,
                workspace_id=workspace_id,
            )
