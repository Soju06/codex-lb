from __future__ import annotations

from app.db.models import OAuthLivePolicy
from app.modules.oauth_live.repository import OAuthLivePolicyRepository
from app.modules.oauth_live.schemas import OAuthLivePolicyResponse, OAuthLivePolicyUpdateRequest


class OAuthLivePolicyAccountNotFoundError(ValueError):
    pass


class OAuthLivePolicyValidationError(ValueError):
    pass


class OAuthLivePolicyService:
    def __init__(self, repository: OAuthLivePolicyRepository) -> None:
        self._repository = repository

    async def get_policy(self, caller_account_id: str) -> OAuthLivePolicyResponse:
        if not await self._repository.account_exists(caller_account_id):
            raise OAuthLivePolicyAccountNotFoundError("Account not found")
        row = await self._repository.get_policy(caller_account_id)
        if row is None:
            return OAuthLivePolicyResponse(
                caller_account_id=caller_account_id,
                is_active=False,
                allowed_account_ids=[],
                created_at=None,
                updated_at=None,
            )
        return _to_response(row)

    async def update_policy(
        self,
        caller_account_id: str,
        payload: OAuthLivePolicyUpdateRequest,
    ) -> OAuthLivePolicyResponse:
        if not await self._repository.account_exists(caller_account_id):
            raise OAuthLivePolicyAccountNotFoundError("Account not found")

        allowed_account_ids = sorted(
            {account_id.strip() for account_id in payload.allowed_account_ids if account_id.strip()}
        )
        if payload.is_active and not allowed_account_ids:
            raise OAuthLivePolicyValidationError("An active OAuth Live policy requires at least one allowed account")

        existing_ids = await self._repository.existing_account_ids(allowed_account_ids)
        if len(existing_ids) != len(allowed_account_ids):
            raise OAuthLivePolicyValidationError("OAuth Live policy contains an unknown allowed account")

        try:
            row = await self._repository.replace_policy(
                caller_account_id,
                is_active=payload.is_active,
                allowed_account_ids=allowed_account_ids,
            )
        except Exception:
            await self._repository.rollback()
            raise
        return _to_response(row)


def _to_response(row: OAuthLivePolicy) -> OAuthLivePolicyResponse:
    return OAuthLivePolicyResponse(
        caller_account_id=row.caller_account_id,
        is_active=row.is_active,
        allowed_account_ids=sorted(assignment.allowed_account_id for assignment in row.allowed_accounts),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
