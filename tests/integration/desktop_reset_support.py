from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditError,
    ConsumeResetCreditResponse,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.core.clients.usage import UsageFetchError
from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import UsageRepository

HEADERS = {"Authorization": "Bearer original-token", "chatgpt-account-id": "original-account"}
ORIGINAL = {
    "user_id": "original-user",
    "account_id": "original-account",
    "plan_type": "plus",
    "rate_limit": {"allowed": False, "limit_reached": True},
    "rate_limit_reset_credits": {"available_count": 1, "credits": []},
}


@dataclass
class FakeResetUpstream:
    credits: dict[str, list[ResetCreditItem]] = field(default_factory=dict)
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    spent: dict[tuple[str, str], str] = field(default_factory=dict)
    lose_response: bool = False
    rejection: str | None = None
    fail_owner: str | None = None

    async def fetch(self, token, account_id, **kwargs):
        owner = "primary" if account_id == "original-account" else "second"
        assert token == "token:" + owner
        if self.fail_owner == owner:
            raise RuntimeError("Synthetic upstream unavailable")
        credits = [credit.model_copy(deep=True) for credit in self.credits[owner]]
        return ResetCreditsResponse(available_count=sum(c.status == "available" for c in credits), credits=credits)

    async def consume(self, token, account_id, credit_id, *, redeem_request_id, **kwargs):
        owner = "primary" if account_id == "original-account" else "second"
        assert token == "token:" + owner
        self.calls.append((owner, credit_id, redeem_request_id))
        if self.rejection:
            return ConsumeResetCreditResponse.model_validate({"code": self.rejection, "credit": {}, "windows_reset": 0})
        key = (owner, redeem_request_id)
        if key in self.spent:
            assert self.spent[key] == credit_id
            code = "already_redeemed"
        else:
            selected = next(c for c in self.credits[owner] if c.id == credit_id)
            assert selected.status == "available"
            selected.status = "redeemed"
            self.spent[key] = credit_id
            if self.lose_response:
                self.lose_response = False
                raise ConsumeResetCreditError(0, "Synthetic lost response")
            code = "reset"
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": code,
                "windows_reset": 1,
                "credit": {"id": credit_id, "reset_type": "codexRateLimits", "status": "redeemed"},
            }
        )


async def seed(monkeypatch, *, pooled=True):
    now = utcnow()
    encryptor = TokenEncryptor()
    fake = FakeResetUpstream()
    async with SessionLocal() as session:
        settings = await SettingsRepository(session).get_or_create()
        settings.desktop_reset_pool_enabled = pooled
        await session.commit()
        for owner, account_id, days in [("primary", "original-account", 7), ("second", "second-account", 1)]:
            await AccountsRepository(session).upsert(
                Account(
                    id=owner,
                    chatgpt_account_id=account_id,
                    email=owner + "@example.test",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("token:" + owner),
                    refresh_token_encrypted=encryptor.encrypt("unused-refresh"),
                    id_token_encrypted=encryptor.encrypt("unused-id"),
                    last_refresh=now,
                    status=AccountStatus.ACTIVE,
                )
            )
            fake.credits[owner] = [
                ResetCreditItem(
                    id=owner + "-credit",
                    status="available",
                    reset_type="codexRateLimits",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=days),
                )
            ]
            for window, minutes in [("primary", 300), ("secondary", 10080)]:
                await UsageRepository(session).add_entry(
                    owner,
                    50,
                    window=window,
                    window_minutes=minutes,
                    reset_at=int(datetime.now(timezone.utc).timestamp()) + minutes * 60,
                )

    async def original(*, access_token, account_id, **kwargs):
        if access_token != "original-token" or account_id != "original-account":
            raise UsageFetchError(401, "Synthetic invalid token")
        return UsagePayload.from_upstream(ORIGINAL)

    async def no_route(*args, **kwargs):
        return None

    async def refreshed(*args, **kwargs):
        return True

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", original)
    monkeypatch.setattr("app.modules.desktop_resets.inventory.fetch_reset_credits", fake.fetch)
    monkeypatch.setattr("app.modules.rate_limit_reset_credits.api.fetch_reset_credits", fake.fetch)
    monkeypatch.setattr("app.modules.desktop_resets.service.consume_reset_credit", fake.consume)
    monkeypatch.setattr("app.modules.desktop_resets.inventory._resolve_upstream_route_for_account", no_route)
    monkeypatch.setattr("app.modules.desktop_resets.service._resolve_upstream_route_for_account", no_route)
    monkeypatch.setattr("app.modules.desktop_resets.service._refresh_usage", refreshed)
    monkeypatch.setattr("app.modules.usage.updater.UsageUpdater.refresh_accounts", refreshed)
    return fake
