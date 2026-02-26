from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog, UsageHistory
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

_PLACEHOLDER_TOKEN = "anthropic-provider"


class AnthropicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._accounts = AccountsRepository(session)
        self._request_logs = RequestLogsRepository(session)
        self._usage = UsageRepository(session)
        self._encryptor = TokenEncryptor()

    async def ensure_provider_account(
        self,
        *,
        account_id: str,
        email: str,
        plan_type: str,
    ) -> Account:
        existing = await self._accounts.get_by_id(account_id)
        if existing is not None:
            if existing.status != AccountStatus.ACTIVE:
                await self._accounts.update_status(account_id, AccountStatus.ACTIVE, None)
                existing.status = AccountStatus.ACTIVE
                existing.deactivation_reason = None
            return existing

        now = utcnow()
        encrypted = self._encryptor.encrypt(_PLACEHOLDER_TOKEN)
        created = Account(
            id=account_id,
            chatgpt_account_id=None,
            email=email,
            plan_type=plan_type,
            access_token_encrypted=encrypted,
            refresh_token_encrypted=encrypted,
            id_token_encrypted=encrypted,
            last_refresh=now,
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )
        return await self._accounts.upsert(created, merge_by_email=False)

    async def add_request_log(
        self,
        *,
        account_id: str,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        error_message: str | None,
        api_key_id: str | None,
        requested_at: datetime | None = None,
    ) -> RequestLog:
        return await self._request_logs.add_log(
            account_id=account_id,
            request_id=request_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            latency_ms=latency_ms,
            status=status,
            error_code=error_code,
            error_message=error_message,
            api_key_id=api_key_id,
            requested_at=requested_at,
        )

    async def add_usage_entry(
        self,
        *,
        account_id: str,
        used_percent: float,
        window: str,
        reset_at: int | None,
        window_minutes: int,
    ) -> UsageHistory:
        entry = await self._usage.add_entry(
            account_id=account_id,
            used_percent=used_percent,
            window=window,
            reset_at=reset_at,
            window_minutes=window_minutes,
        )
        return entry
