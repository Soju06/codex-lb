from __future__ import annotations

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditCredit,
    ConsumeResetCreditResponse,
    ResetCreditsResponse,
    consume_reset_credit,
)
from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardConflictError, ProxyAuthError, ProxyConflictError
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.db.models import Account
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.background_repository import BackgroundAccountsRepository
from app.modules.desktop_resets.inventory import CreditInventory
from app.modules.desktop_resets.projection import ResetPoolUnavailable, eligible, inventory, pool_credits
from app.modules.desktop_resets.repository import RedemptionPin, RedemptionRepository
from app.modules.desktop_usage.identity import DesktopIdentity
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.background_repository import BackgroundAdditionalUsageRepository, BackgroundUsageRepository
from app.modules.usage.updater import UsageUpdater, _resolve_upstream_route_for_account


class ResetRequestConflict(ProxyConflictError):
    status_code = 409
    code = "reset_credit_request_conflict"
    message = "This reset attempt is already bound to another credit"
    error_type = "invalid_request_error"


def no_credit() -> ConsumeResetCreditResponse:
    return ConsumeResetCreditResponse(code="no_credit", credit=ConsumeResetCreditCredit(), windows_reset=0)


class DesktopResetService:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repos = repo_factory
        self._inventory = CreditInventory(repo_factory)

    async def enabled(self) -> bool:
        async with self._repos() as repos:
            if repos.session is None:
                raise RuntimeError("Desktop resets require a database session")
            return (await SettingsRepository(repos.session).get_or_create()).desktop_reset_pool_enabled

    async def list_credits(self, identity: DesktopIdentity, *, cached_only: bool = False) -> ResetCreditsResponse:
        pooled = await self.enabled()
        snapshots = await self._inventory.load(identity, pooled=pooled, cached_only=cached_only)
        if not pooled:
            snapshot = snapshots[identity.account_id]
            return ResetCreditsResponse(available_count=snapshot.available_count, credits=snapshot.credits)
        return inventory(pool_credits(snapshots, now=datetime.now(timezone.utc)))

    async def consume(
        self, identity: DesktopIdentity, *, request_id: str, credit_id: str | None
    ) -> ConsumeResetCreditResponse:
        async with self._repos() as repos:
            if repos.session is None:
                raise RuntimeError("Desktop resets require a database session")
            pinned = await RedemptionRepository(repos.session).get(identity.chatgpt_account_id, request_id)
        if pinned is None:
            credits = await self._inventory.available(identity, pooled=await self.enabled(), force=True)
            selected = (
                next((item for item in credits if item.credit.id == credit_id), None)
                if credit_id
                else next(iter(credits), None)
            )
            if selected is None:
                return no_credit()
            async with self._repos() as repos:
                if repos.session is None:
                    raise RuntimeError("Desktop resets require a database session")
                owner = await repos.accounts.get_by_id(selected.owner_id)
                if owner is None or not eligible(owner) or owner.chatgpt_account_id is None:
                    raise ResetPoolUnavailable("The selected reset owner is no longer eligible")
                pinned = await RedemptionRepository(repos.session).pin(
                    identity.chatgpt_account_id,
                    request_id,
                    RedemptionPin(selected.owner_id, selected.credit.id, owner.chatgpt_account_id),
                )
        if credit_id is not None and credit_id != pinned.credit_id:
            raise ResetRequestConflict()
        return await self._consume_pinned(identity, pinned, request_id)

    async def _check_admission(
        self, identity: DesktopIdentity, pin: RedemptionPin, upstream_account_id: str | None
    ) -> None:
        async with self._repos() as repos:
            if repos.session is None:
                raise RuntimeError("Desktop resets require a database session")
            caller = await repos.accounts.get_by_id(identity.account_id)
            owner = await repos.accounts.get_by_id(pin.owner_id)
            policy = await SettingsRepository(repos.session).get_or_create()
            if caller is None or not eligible(caller) or caller.chatgpt_account_id != identity.chatgpt_account_id:
                raise ProxyAuthError("The signed-in account is no longer eligible")
            if (
                owner is None
                or not eligible(owner)
                or owner.chatgpt_account_id != pin.owner_chatgpt_account_id
                or upstream_account_id != pin.owner_chatgpt_account_id
            ):
                raise ResetPoolUnavailable("The selected reset owner is no longer eligible")
            if not policy.desktop_reset_pool_enabled and pin.owner_id != identity.account_id:
                raise ProxyAuthError("Cross-account reset pooling is disabled")

    async def _consume_pinned(
        self, identity: DesktopIdentity, pin: RedemptionPin, request_id: str
    ) -> ConsumeResetCreditResponse:
        # The dashboard and scheduler already share this account-level serializer.
        from app.modules.rate_limit_reset_credits.api import _redeem_soonest_reset_credit

        async def consume_checked(
            token: str,
            upstream_id: str | None,
            selected_credit: str,
            *,
            redeem_request_id: str,
            route: ResolvedUpstreamRoute | None,
            allow_direct_egress: bool,
        ) -> ConsumeResetCreditResponse:
            await self._check_admission(identity, pin, upstream_id)
            if selected_credit != pin.credit_id:
                raise ResetRequestConflict()
            return await consume_reset_credit(
                token,
                upstream_id,
                selected_credit,
                redeem_request_id=redeem_request_id,
                route=route,
                allow_direct_egress=allow_direct_egress,
            )

        async with self._repos() as repos:
            account = await repos.accounts.get_by_id(pin.owner_id)
            if account is None or not eligible(account):
                raise ResetPoolUnavailable("The selected reset owner is no longer eligible")
            await self._check_admission(identity, pin, account.chatgpt_account_id)
            try:
                result = await _redeem_soonest_reset_credit(
                    account=account,
                    store=self._inventory.store,
                    encryptor=TokenEncryptor(),
                    lock_session=repos.session,
                    auth_manager=AuthManager(repos.accounts, refresh_repo_factory=self._inventory.account_repo),
                    consume_fn=consume_checked,
                    refresh_usage=_refresh_usage,
                    resolve_route=_consume_route,
                    redeem_request_id=(
                        request_id
                        if pin.owner_id == identity.account_id
                        else str(
                            uuid5(NAMESPACE_URL, f"codex-lb:desktop-reset:{identity.chatgpt_account_id}:{request_id}")
                        )
                    ),
                    expected_credit_id=pin.credit_id,
                    bound_credit_id=pin.credit_id,
                )
            except DashboardConflictError as error:
                if error.code == "reset_credit_request_conflict":
                    raise ResetRequestConflict() from None
                if error.code == "no_available_reset_credit":
                    return no_credit()
                raise ResetPoolUnavailable("Reset credit redemption could not complete") from None
            return result.upstream


async def _consume_route(account: Account) -> ResolvedUpstreamRoute | None:
    return await _resolve_upstream_route_for_account(account, operation="rate_limit_reset_consume")


async def _refresh_usage(account: Account) -> None:
    updater = UsageUpdater(
        BackgroundUsageRepository(), BackgroundAccountsRepository(), BackgroundAdditionalUsageRepository()
    )
    await updater.force_refresh(account)
    get_account_selection_cache().invalidate()
