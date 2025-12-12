from __future__ import annotations

from app.core.crypto import TokenEncryptor
from app.core.auth.refresh import RefreshError, refresh_access_token, should_refresh
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository


class AuthManager:
    def __init__(self, repo: AccountsRepository) -> None:
        self._repo = repo
        self._encryptor = TokenEncryptor()

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        if force or should_refresh(account.last_refresh):
            return await self.refresh_account(account)
        return account

    async def refresh_account(self, account: Account) -> Account:
        refresh_token = self._encryptor.decrypt(account.refresh_token_encrypted)
        try:
            result = await refresh_access_token(refresh_token)
        except RefreshError as exc:
            if exc.is_permanent:
                reason = PERMANENT_FAILURE_CODES.get(exc.code, exc.message)
                await self._repo.update_status(account.id, AccountStatus.DEACTIVATED, reason)
                account.status = AccountStatus.DEACTIVATED
                account.deactivation_reason = reason
            raise

        account.access_token_encrypted = self._encryptor.encrypt(result.access_token)
        account.refresh_token_encrypted = self._encryptor.encrypt(result.refresh_token)
        account.id_token_encrypted = self._encryptor.encrypt(result.id_token)
        account.last_refresh = utcnow()
        if result.plan_type:
            account.plan_type = result.plan_type
        if result.email:
            account.email = result.email

        await self._repo.update_tokens(
            account.id,
            access_token_encrypted=account.access_token_encrypted,
            refresh_token_encrypted=account.refresh_token_encrypted,
            id_token_encrypted=account.id_token_encrypted,
            last_refresh=account.last_refresh,
            plan_type=account.plan_type,
            email=account.email,
        )
        return account
